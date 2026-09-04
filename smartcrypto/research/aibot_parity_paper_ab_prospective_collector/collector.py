"""Prospective collector for AIBOT Parity Paper A/B research evidence.

The collector is deliberately read-only toward AIBOT, Decision Ledger and Paper
closed-trade sources. It captures immutable decision-time observations under
``data/reports/aibot_parity`` and resolves outcomes later through the explicit
Decision Ledger 4.2 ``candidate_id -> trade_id`` link. It never infers trade
links from symbol/time proximity and never changes Paper runtime behaviour.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from smartcrypto.execution.decision_ledger_v4_2.contracts import (
    DecisionRecordV42,
    TradeLinkRecordV42,
    parse_payload_record,
)
from smartcrypto.research.aibot_parity_orchestrator.contracts import (
    REQUIRED_SOURCE_NAMES,
    AibotParityPipelineSnapshot,
    PointInTimeStatus,
    canonical_sha256,
)
from smartcrypto.research.aibot_parity_paper_ab_soak.evaluator import (
    Preregistration,
    evaluate_prospective_ab_soak,
)
from smartcrypto.research.paper_closed_trades_readonly_source_contract.source_contract import (
    load_closed_trade_source_candidates,
    normalize_closed_trade_rows,
)
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_text,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    _InterProcessFileLock,
)

SCHEMA_VERSION = "aibot_parity_paper_ab_prospective_collector_v1"
LEGACY_OBSERVATION_SCHEMA_VERSION = "aibot_parity_paper_ab_prospective_observation_v1"
OBSERVATION_SCHEMA_VERSION = "aibot_parity_paper_ab_prospective_observation_v2"
DEFAULT_OBSERVATIONS = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_observations_v1.jsonl"
)
ALLOWED_ACTIONS = frozenset({"ACCEPT", "REJECT", "ABSTAIN"})

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "read_only_runtime_sources": True,
    "operational_authority": False,
    "traffic_split_performed": False,
    "paper_behavior_changed": False,
    "treatment_runtime_assignment_performed": False,
    "writes_active_signals": False,
    "signal_published": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "changes_strategy": False,
    "changes_risk": False,
    "changes_stake": False,
    "changes_leverage": False,
    "changes_roi": False,
    "changes_stoploss": False,
    "changes_universe": False,
    "changes_model": False,
    "paper_treatment_release_allowed": False,
    "paper_activation_performed": False,
    "qlib_security_gate_bypassed": False,
}

_REQUIRED_SNAPSHOT_TRUE = ("paper_only", "shadow_only", "research_only")
_REQUIRED_SNAPSHOT_FALSE = (
    "operational_authority",
    "writes_active_signals",
    "signal_published",
    "sends_orders",
    "exchange_private_access",
    "changes_risk",
    "changes_model",
    "live_release_allowed",
    "canary_release_allowed",
)


@dataclass(frozen=True)
class DecisionLedgerRows:
    decisions: tuple[DecisionRecordV42, ...]
    trade_links: tuple[TradeLinkRecordV42, ...]


@dataclass(frozen=True)
class CollectionResult:
    report: dict[str, Any]
    observations: list[dict[str, Any]]
    candidate_rows: list[dict[str, Any]]
    assignments: list[dict[str, Any]]


def _stable_sha256(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        dict(payload),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _text(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_trade_id(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _normalize_symbol(value: object) -> str:
    # Freqtrade Futures pairs include a settlement suffix (for example
    # BTC/USDT:USDT), while Decision Ledger stores BTCUSDT. The explicit
    # trade_id remains the authoritative join; this normalization is a
    # secondary lineage check only.
    pair_without_settlement = str(value or "").upper().strip().split(":", 1)[0]
    return (
        pair_without_settlement.replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .strip()
    )


def _normalize_side(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"buy", "long", "comprado"}:
        return "long"
    if text in {"sell", "short", "vendido"}:
        return "short"
    return text


def load_aibot_snapshots(path: str | Path) -> list[AibotParityPipelineSnapshot]:
    """Load one snapshot or an explicit snapshot collection."""

    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_rows: list[Mapping[str, Any]] = []
    if isinstance(payload, list):
        raw_rows = [row for row in payload if isinstance(row, Mapping)]
    elif isinstance(payload, Mapping):
        if payload.get("schema_version") == "aibot_parity_e2e_snapshot_v1":
            raw_rows = [payload]
        else:
            for key in ("snapshots", "rows", "records"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    raw_rows = [row for row in candidate if isinstance(row, Mapping)]
                    break
    if not raw_rows:
        raise ValueError("aibot_snapshot_payload_has_no_snapshots")
    return [AibotParityPipelineSnapshot.model_validate(row) for row in raw_rows]


def load_decision_ledger_jsonl(path: str | Path) -> DecisionLedgerRows:
    """Read and cryptographically validate Decision Ledger 4.2 JSONL records."""

    source = Path(path)
    decisions: list[DecisionRecordV42] = []
    trade_links: list[TradeLinkRecordV42] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = parse_payload_record(line)
        except ValueError as exc:
            raise ValueError(f"decision_ledger_invalid_line:{line_number}") from exc
        if isinstance(record, DecisionRecordV42):
            decisions.append(record)
        elif isinstance(record, TradeLinkRecordV42):
            trade_links.append(record)
    if not decisions and not trade_links:
        raise ValueError("decision_ledger_has_no_records")
    return DecisionLedgerRows(tuple(decisions), tuple(trade_links))


def load_normalized_closed_trades(
    *, project_root: str | Path, source_path: str | Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reuse the certified read-only closed-trade source contract."""

    root = Path(project_root).resolve()
    loaded = load_closed_trade_source_candidates(
        project_root=root,
        allow_runtime_read=True,
        source_paths=[source_path],
    )
    selected = next(
        (
            candidate
            for candidate in loaded.candidates
            if candidate.status == "ok" and candidate.rows
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"closed_trade_source_unavailable:{loaded.source_reason}")
    normalized, rejected, mapping = normalize_closed_trade_rows(
        selected.rows,
        source_path=str(selected.path),
        source_sha256=selected.sha256,
    )
    if not normalized:
        raise ValueError("closed_trade_source_has_no_contract_valid_rows")
    diagnostics = {
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "source_sha256": selected.sha256,
        "normalized_closed_trade_count": len(normalized),
        "rejected_row_count": len(rejected),
        "canonical_field_mapping": mapping,
    }
    return normalized, diagnostics


def _snapshot_point_in_time_valid(snapshot: AibotParityPipelineSnapshot) -> bool:
    if snapshot.missing_required_sources:
        return False
    required = set(REQUIRED_SOURCE_NAMES)
    required_views = {
        view.source_name: view for view in snapshot.source_views if view.source_name in required
    }
    if set(required_views) != required:
        return False
    return all(
        view.point_in_time_status is PointInTimeStatus.VALID
        for view in required_views.values()
    )


def _snapshot_safety_valid(snapshot: AibotParityPipelineSnapshot) -> bool:
    safety = snapshot.safety
    return all(safety.get(field) is True for field in _REQUIRED_SNAPSHOT_TRUE) and all(
        safety.get(field) is False for field in _REQUIRED_SNAPSHOT_FALSE
    )


def _decision_index(
    decisions: Sequence[DecisionRecordV42], blockers: list[str]
) -> dict[str, DecisionRecordV42]:
    indexed: dict[str, DecisionRecordV42] = {}
    for record in decisions:
        prior = indexed.get(record.candidate_id)
        if prior is not None and prior.payload_sha256 != record.payload_sha256:
            blockers.append(f"DECISION_CANDIDATE_CONFLICT:{record.candidate_id}")
            continue
        indexed[record.candidate_id] = record
    return indexed


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, field: str) -> datetime:
    text = _text(value)
    if text is None:
        raise ValueError(f"{field}_missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _new_collector_run_id() -> str:
    return f"collector-run-{uuid.uuid4().hex}"


def capture_observations(
    *,
    snapshots: Sequence[AibotParityPipelineSnapshot],
    decisions: Sequence[DecisionRecordV42],
    financial_config_unchanged: bool,
    paper_financial_config_sha256: str | None = None,
    expected_financial_config_sha256: str | None = None,
    captured_at_utc: datetime | None = None,
    collector_run_id: str | None = None,
    existing_observations: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Capture immutable decision-time observations without outcome fields."""

    blockers: list[str] = []
    decision_by_candidate = _decision_index(decisions, blockers)
    existing_ids = {str(row.get("observation_id")) for row in existing_observations}
    captured_at = captured_at_utc or datetime.now(UTC)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at_utc_must_be_timezone_aware")
    captured_at = captured_at.astimezone(UTC)
    run_id = _text(collector_run_id) or _new_collector_run_id()
    current_fingerprint = _text(paper_financial_config_sha256)
    expected_fingerprint = _text(expected_financial_config_sha256)

    observations: list[dict[str, Any]] = []
    for snapshot in snapshots:
        action = snapshot.ensemble_action.strip().upper()
        if action not in ALLOWED_ACTIONS:
            blockers.append(f"TREATMENT_ACTION_NOT_CANONICAL:{snapshot.cycle_id}")
            continue
        if snapshot.status.value == "BLOCKED":
            blockers.append(f"AIBOT_SNAPSHOT_BLOCKED:{snapshot.cycle_id}")
            continue
        if not _snapshot_point_in_time_valid(snapshot):
            blockers.append(
                f"AIBOT_SNAPSHOT_POINT_IN_TIME_NOT_VALID:{snapshot.cycle_id}"
            )
            continue
        if captured_at < snapshot.decision_time_utc:
            blockers.append(f"CAPTURE_BEFORE_DECISION:{snapshot.cycle_id}")
            continue
        if not financial_config_unchanged:
            blockers.append(f"FINANCIAL_CONFIG_PARITY_NOT_PROVEN:{snapshot.cycle_id}")
            continue
        if current_fingerprint is None or expected_fingerprint is None:
            blockers.append(
                f"FINANCIAL_CONFIG_FINGERPRINT_NOT_PROVIDED:{snapshot.cycle_id}"
            )
            continue
        if current_fingerprint != expected_fingerprint:
            blockers.append(f"FINANCIAL_CONFIG_FINGERPRINT_MISMATCH:{snapshot.cycle_id}")
            continue
        if not _snapshot_safety_valid(snapshot):
            blockers.append(f"AIBOT_SNAPSHOT_SAFETY_NOT_PROVEN:{snapshot.cycle_id}")
            continue
        for candidate_id in snapshot.selected_candidate_ids:
            observation_id = "obs-" + hashlib.sha256(
                f"{snapshot.cycle_id}|{candidate_id}".encode("utf-8")
            ).hexdigest()
            if observation_id in existing_ids:
                continue
            decision = decision_by_candidate.get(candidate_id)
            if decision is None:
                blockers.append(f"DECISION_LEDGER_CANDIDATE_MISSING:{candidate_id}")
                continue
            if decision.decision_timestamp > snapshot.decision_time_utc:
                blockers.append(f"DECISION_AFTER_AIBOT_SNAPSHOT:{candidate_id}")
                continue
            if action == "ACCEPT" and snapshot.riskmanager_shadow_decision != "ALLOW":
                blockers.append(f"ACCEPT_WITHOUT_SHADOW_RISK_ALLOW:{candidate_id}")
                continue
            observation_body: dict[str, Any] = {
                "schema_version": OBSERVATION_SCHEMA_VERSION,
                "observation_id": observation_id,
                "candidate_id": candidate_id,
                "cycle_id": snapshot.cycle_id,
                "observed_at_utc": _iso_utc(snapshot.decision_time_utc),
                "captured_at_utc": _iso_utc(captured_at),
                "collector_run_id": run_id,
                "treatment_action": action,
                "riskmanager_shadow_decision": snapshot.riskmanager_shadow_decision,
                "symbol": decision.symbol,
                "side": decision.side.value,
                "regime": decision.regime,
                "qlib_status": snapshot.qlib_status,
                "point_in_time_valid": True,
                "financial_config_unchanged": True,
                "paper_financial_config_sha256": current_fingerprint,
                "paper_only": True,
                "shadow_only": True,
                "operational_authority": False,
                "signal_published": False,
                "writes_active_signals": False,
                "sends_orders": False,
                "changes_risk": False,
                "changes_model": False,
                "aibot_snapshot_sha256": canonical_sha256(
                    snapshot.model_dump(mode="json")
                ),
                "decision_payload_sha256": decision.payload_sha256,
            }
            observation_body["observation_sha256"] = _stable_sha256(observation_body)
            observations.append(observation_body)
            existing_ids.add(observation_id)
    return observations, list(dict.fromkeys(blockers))


def _validate_observation(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    seal = _text(payload.pop("observation_sha256", None))
    if seal is None or seal != _stable_sha256(payload):
        raise ValueError("observation_sha256_mismatch")
    schema_version = payload.get("schema_version")
    if schema_version not in {OBSERVATION_SCHEMA_VERSION, LEGACY_OBSERVATION_SCHEMA_VERSION}:
        raise ValueError("observation_schema_version_invalid")
    required_text = [
        "observation_id",
        "candidate_id",
        "cycle_id",
        "observed_at_utc",
        "treatment_action",
        "riskmanager_shadow_decision",
        "decision_payload_sha256",
        "aibot_snapshot_sha256",
    ]
    if schema_version == OBSERVATION_SCHEMA_VERSION:
        required_text.extend(
            (
                "captured_at_utc",
                "collector_run_id",
                "paper_financial_config_sha256",
            )
        )
    if any(_text(payload.get(field)) is None for field in required_text):
        raise ValueError("observation_required_field_missing")
    observed = _parse_utc(payload.get("observed_at_utc"), field="observed_at_utc")
    if schema_version == OBSERVATION_SCHEMA_VERSION:
        captured = _parse_utc(payload.get("captured_at_utc"), field="captured_at_utc")
        if captured < observed:
            raise ValueError("captured_at_utc_before_observed_at_utc")
        fingerprint = str(payload.get("paper_financial_config_sha256"))
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("paper_financial_config_sha256_invalid")
    payload["observation_sha256"] = seal
    return payload


def read_observation_ledger(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    if source.is_symlink() or not source.is_file():
        raise ValueError("observation_ledger_not_regular_file")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"observation_ledger_invalid_json:{line_number}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"observation_ledger_invalid_row:{line_number}")
        try:
            rows.append(_validate_observation(payload))
        except ValueError as exc:
            raise ValueError(f"observation_ledger_invalid_row:{line_number}:{exc}") from exc
    return rows


def _resolve_observation_path(root: Path, value: str | Path) -> Path:
    target = Path(value)
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    allowed = (root / "data" / "reports" / "aibot_parity").resolve()
    try:
        target.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("observations_output_must_be_under_aibot_parity_reports") from exc
    if target.suffix.lower() != ".jsonl":
        raise ValueError("observations_output_must_use_jsonl_suffix")
    return target


def write_observations_idempotent(
    *,
    project_root: str | Path,
    path: str | Path,
    observations: Iterable[Mapping[str, Any]],
) -> int:
    """Merge immutable observations under a serialized atomic write."""

    root = Path(project_root).resolve()
    target = _resolve_observation_path(root, path)
    incoming = [_validate_observation(row) for row in observations]
    if not incoming:
        return 0
    policy = AtomicWritePolicy.restricted(
        [(root / "data" / "reports" / "aibot_parity").resolve()],
        working_directory=root,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _InterProcessFileLock(
        target.parent / f".{target.name}.collector.lock",
        timeout_seconds=policy.lock_timeout_seconds,
    )
    lock.acquire()
    try:
        existing = read_observation_ledger(target)
        by_id: dict[str, dict[str, Any]] = {}
        candidate_cycles: dict[str, str] = {}
        for row in [*existing, *incoming]:
            observation_id = str(row["observation_id"])
            prior = by_id.get(observation_id)
            if prior is not None and prior != row:
                raise ValueError("observation_id_conflict")
            candidate_id = str(row["candidate_id"])
            cycle_id = str(row["cycle_id"])
            prior_cycle = candidate_cycles.get(candidate_id)
            if prior_cycle is not None and prior_cycle != cycle_id:
                raise ValueError(f"candidate_id_reused_across_cycles:{candidate_id}")
            candidate_cycles[candidate_id] = cycle_id
            by_id[observation_id] = row
        appended = len(by_id) - len({str(row["observation_id"]) for row in existing})
        if appended:
            rendered = "".join(
                json.dumps(
                    row,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
                for row in by_id.values()
            )
            atomic_write_text(target, rendered, policy=policy)
        return appended
    finally:
        lock.release()


def merge_observations(
    existing: Sequence[Mapping[str, Any]], incoming: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    candidate_cycles: dict[str, str] = {}
    for raw in [*existing, *incoming]:
        row = _validate_observation(raw)
        observation_id = str(row["observation_id"])
        prior = by_id.get(observation_id)
        if prior is not None and prior != row:
            blockers.append(f"OBSERVATION_ID_CONFLICT:{observation_id}")
            continue
        candidate_id = str(row["candidate_id"])
        cycle_id = str(row["cycle_id"])
        prior_cycle = candidate_cycles.get(candidate_id)
        if prior_cycle is not None and prior_cycle != cycle_id:
            blockers.append(f"CANDIDATE_ID_REUSED_ACROSS_CYCLES:{candidate_id}")
            continue
        candidate_cycles[candidate_id] = cycle_id
        by_id[observation_id] = row
    ordered = sorted(
        by_id.values(),
        key=lambda row: (str(row["observed_at_utc"]), str(row["candidate_id"])),
    )
    return ordered, list(dict.fromkeys(blockers))



_ASSIGNMENT_OUTCOME_FIELDS = frozenset(
    {
        "outcome_available_at_utc",
        "realized_net_pnl_usdt",
        "effective_arm_pnl_usdt",
    }
)


def immutable_assignment_rows(
    assignments: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project evaluator assignments onto their immutable analytical identity."""

    return [
        {
            key: value
            for key, value in dict(row).items()
            if key not in _ASSIGNMENT_OUTCOME_FIELDS
        }
        for row in assignments
    ]


def _trade_link_index(
    trade_links: Sequence[TradeLinkRecordV42], blockers: list[str]
) -> dict[str, TradeLinkRecordV42]:
    indexed: dict[str, TradeLinkRecordV42] = {}
    for link in trade_links:
        prior = indexed.get(link.candidate_id)
        if prior is not None and prior.trade_id != link.trade_id:
            blockers.append(f"TRADE_LINK_CONFLICT:{link.candidate_id}")
            continue
        indexed[link.candidate_id] = link
    return indexed


def _closed_trade_index(
    closed_trades: Sequence[Mapping[str, Any]], blockers: list[str]
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in closed_trades:
        row = dict(raw)
        trade_id = _normalize_trade_id(row.get("trade_id"))
        if trade_id is None:
            blockers.append("CLOSED_TRADE_ID_MISSING")
            continue
        prior = indexed.get(trade_id)
        if prior is not None and prior.get("row_fingerprint") != row.get("row_fingerprint"):
            blockers.append(f"CLOSED_TRADE_CONFLICT:{trade_id}")
            continue
        indexed[trade_id] = row
    return indexed


def materialize_candidate_rows(
    *,
    observations: Sequence[Mapping[str, Any]],
    trade_links: Sequence[TradeLinkRecordV42],
    closed_trades: Sequence[Mapping[str, Any]],
    as_of_utc: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Attach later Paper outcomes only through the sealed trade-link contract."""

    blockers: list[str] = []
    link_by_candidate = _trade_link_index(trade_links, blockers)
    trade_by_id = _closed_trade_index(closed_trades, blockers)
    rows: list[dict[str, Any]] = []
    pending_trade_link_count = 0
    pending_closed_trade_count = 0
    completed_outcome_count = 0
    legacy_observation_excluded_count = 0

    for raw in observations:
        observation = _validate_observation(raw)
        if observation.get("schema_version") == LEGACY_OBSERVATION_SCHEMA_VERSION:
            legacy_observation_excluded_count += 1
            continue
        candidate_row = {
            key: observation.get(key)
            for key in (
                "candidate_id",
                "cycle_id",
                "observed_at_utc",
                "captured_at_utc",
                "collector_run_id",
                "paper_financial_config_sha256",
                "treatment_action",
                "riskmanager_shadow_decision",
                "symbol",
                "side",
                "regime",
                "qlib_status",
                "point_in_time_valid",
                "financial_config_unchanged",
                "paper_only",
                "shadow_only",
                "operational_authority",
                "signal_published",
                "writes_active_signals",
                "sends_orders",
                "changes_risk",
                "changes_model",
            )
        }
        candidate_id = str(observation["candidate_id"])
        link = link_by_candidate.get(candidate_id)
        if link is None:
            pending_trade_link_count += 1
            rows.append(candidate_row)
            continue
        trade_id = _normalize_trade_id(link.trade_id)
        trade = trade_by_id.get(str(trade_id)) if trade_id is not None else None
        if trade is None:
            pending_closed_trade_count += 1
            rows.append(candidate_row)
            continue
        if _normalize_symbol(trade.get("symbol")) != _normalize_symbol(
            observation.get("symbol")
        ):
            blockers.append(f"OUTCOME_SYMBOL_MISMATCH:{candidate_id}")
            rows.append(candidate_row)
            continue
        if _normalize_side(trade.get("side")) != _normalize_side(observation.get("side")):
            blockers.append(f"OUTCOME_SIDE_MISMATCH:{candidate_id}")
            rows.append(candidate_row)
            continue
        pnl = _finite_float(trade.get("pnl"))
        close_time = _text(trade.get("close_time"))
        if pnl is None or close_time is None:
            blockers.append(f"OUTCOME_PAYLOAD_INCOMPLETE:{candidate_id}")
            rows.append(candidate_row)
            continue
        try:
            outcome_time = _parse_utc(close_time, field="outcome_available_at_utc")
        except ValueError:
            blockers.append(f"OUTCOME_AVAILABLE_AT_UTC_INVALID:{candidate_id}")
            rows.append(candidate_row)
            continue
        if as_of_utc is not None:
            as_of = as_of_utc.astimezone(UTC)
            if outcome_time > as_of:
                blockers.append(f"OUTCOME_AVAILABLE_AFTER_COLLECTION_RUN:{candidate_id}")
                rows.append(candidate_row)
                continue
        captured_text = _text(observation.get("captured_at_utc"))
        if captured_text is not None:
            captured_time = _parse_utc(captured_text, field="captured_at_utc")
            if outcome_time <= captured_time:
                blockers.append(f"OUTCOME_NOT_AFTER_PROSPECTIVE_CAPTURE:{candidate_id}")
                rows.append(candidate_row)
                continue
        candidate_row["realized_net_pnl_usdt"] = pnl
        candidate_row["outcome_available_at_utc"] = _iso_utc(outcome_time)
        completed_outcome_count += 1
        rows.append(candidate_row)

    counters = {
        "completed_outcome_count": completed_outcome_count,
        "pending_trade_link_count": pending_trade_link_count,
        "pending_closed_trade_count": pending_closed_trade_count,
        "pending_outcome_count": pending_trade_link_count + pending_closed_trade_count,
        "legacy_observation_excluded_count": legacy_observation_excluded_count,
    }
    return rows, list(dict.fromkeys(blockers)), counters


def collect_prospective_evidence(
    *,
    preregistration: Preregistration,
    snapshots: Sequence[AibotParityPipelineSnapshot],
    decisions: Sequence[DecisionRecordV42],
    trade_links: Sequence[TradeLinkRecordV42],
    closed_trades: Sequence[Mapping[str, Any]],
    existing_observations: Sequence[Mapping[str, Any]] = (),
    financial_config_unchanged: bool,
    paper_financial_config_sha256: str | None = None,
    expected_financial_config_sha256: str | None = None,
    captured_at_utc: datetime | None = None,
    collector_run_id: str | None = None,
) -> CollectionResult:
    """Pure orchestration for capture, outcome materialization and A/B evaluation."""

    run_id = _text(collector_run_id) or _new_collector_run_id()
    captured_at = captured_at_utc or datetime.now(UTC)
    new_observations, capture_blockers = capture_observations(
        snapshots=snapshots,
        decisions=decisions,
        financial_config_unchanged=financial_config_unchanged,
        paper_financial_config_sha256=paper_financial_config_sha256,
        expected_financial_config_sha256=expected_financial_config_sha256,
        captured_at_utc=captured_at,
        collector_run_id=run_id,
        existing_observations=existing_observations,
    )
    observations, merge_blockers = merge_observations(
        existing_observations, new_observations
    )
    candidate_rows, outcome_blockers, counters = materialize_candidate_rows(
        observations=observations,
        trade_links=trade_links,
        closed_trades=closed_trades,
        as_of_utc=captured_at,
    )
    ab_report, assignments = evaluate_prospective_ab_soak(
        preregistration, candidate_rows
    )
    ab_integrity_blockers: list[str] = []
    if ab_report.get("status") == "blocked":
        soak_health = ab_report.get("soak_health")
        if isinstance(soak_health, Mapping):
            values = soak_health.get("integrity_blockers")
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                ab_integrity_blockers.extend(
                    f"AB_SOAK_INTEGRITY:{str(value)}" for value in values
                )
        if not ab_integrity_blockers:
            ab_integrity_blockers.append(
                f"AB_SOAK_INTEGRITY:{str(ab_report.get('reason') or 'blocked')}"
            )
    collector_blockers = list(
        dict.fromkeys(
            [
                *capture_blockers,
                *merge_blockers,
                *outcome_blockers,
                *ab_integrity_blockers,
            ]
        )
    )
    candidate_start = (
        min(str(row["observed_at_utc"]) for row in candidate_rows)
        if candidate_rows
        else None
    )
    status = "blocked" if collector_blockers else "ok"
    reason = collector_blockers[0] if collector_blockers else "collector_evidence_materialized"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": "COLLECT_PROSPECTIVE_EVIDENCE",
        "collector_run_id": run_id,
        "captured_at_utc": _iso_utc(captured_at),
        "paper_financial_config_sha256": _text(paper_financial_config_sha256),
        "expected_financial_config_sha256": _text(expected_financial_config_sha256),
        "financial_config_fingerprint_valid": bool(
            paper_financial_config_sha256
            and expected_financial_config_sha256
            and paper_financial_config_sha256 == expected_financial_config_sha256
        ),
        "new_observation_count": len(new_observations),
        "total_observation_count": len(observations),
        "candidate_row_count": len(candidate_rows),
        **counters,
        "decision_record_count": len(decisions),
        "trade_link_record_count": len(trade_links),
        "closed_trade_count": len(closed_trades),
        "collector_blocker_count": len(collector_blockers),
        "collector_blockers": collector_blockers,
        "collection_clock_candidate_start_utc": candidate_start,
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "collection_clock_reason": (
            "software_execution_alone_does_not_prove_paper_host_recurring_collection"
        ),
        "ab_soak": ab_report,
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "observations_appended": 0,
        "assignments_appended": 0,
    }
    return CollectionResult(report, observations, candidate_rows, assignments)


__all__ = [
    "CollectionResult",
    "DEFAULT_OBSERVATIONS",
    "DecisionLedgerRows",
    "LEGACY_OBSERVATION_SCHEMA_VERSION",
    "OBSERVATION_SCHEMA_VERSION",
    "SAFETY_FLAGS",
    "SCHEMA_VERSION",
    "capture_observations",
    "collect_prospective_evidence",
    "immutable_assignment_rows",
    "load_aibot_snapshots",
    "load_decision_ledger_jsonl",
    "load_normalized_closed_trades",
    "materialize_candidate_rows",
    "merge_observations",
    "read_observation_ledger",
    "write_observations_idempotent",
]
