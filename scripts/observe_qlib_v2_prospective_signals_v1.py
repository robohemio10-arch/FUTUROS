#!/usr/bin/env python3
"""Observe and persist identity-safe Qlib V2 prospective Paper signal evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_signal_observer import (
    DEFAULT_ACTIVE_SIGNALS_PATH,
    DEFAULT_DECISION_LEDGER_PATH,
    DEFAULT_FREEZE_SPEC_PATH,
    DEFAULT_LEDGER_PATH,
    LEDGER_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_qlib_v2_prospective_signal_observer_v1,
)
from smartcrypto.learning.paper_autolearning.qlib_v2_prospective_paper_confirmation import (
    _sha256_json,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the certified Qlib V2 freeze and authoritative Paper active signals, "
            "score them before outcomes, and optionally persist a research-only ledger."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--freeze-spec", default=str(DEFAULT_FREEZE_SPEC_PATH))
    parser.add_argument("--active-signals", default=str(DEFAULT_ACTIVE_SIGNALS_PATH))
    parser.add_argument(
        "--decision-ledger",
        default=str(DEFAULT_DECISION_LEDGER_PATH),
        help=(
            "Read-only Decision Ledger V4.2 JSONL used as the authoritative identity "
            "source for candidate/signal/decision correlation."
        ),
    )
    parser.add_argument("--outcome-path", default="data/feedback/outcome_events.parquet")
    parser.add_argument(
        "--market-features-path",
        default="data/features/market_features_60d.parquet",
    )
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--write-ledger", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _merge_ledger(
    *,
    existing: Mapping[str, Any] | None,
    report: Mapping[str, Any],
    recorded_at_utc: datetime | None = None,
) -> dict[str, Any]:
    policy_sha = str(report.get("policy_sha256") or "").strip()
    if not policy_sha:
        raise RuntimeError("observer_policy_sha256_missing")
    recorded_at = _require_utc_datetime(
        recorded_at_utc if recorded_at_utc is not None else datetime.now(UTC),
        "ledger_recorded_at_utc",
    )

    current: list[dict[str, Any]] = []
    if existing is not None:
        if existing.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise RuntimeError("existing_observer_ledger_schema_mismatch")
        if existing.get("policy_sha256") != policy_sha:
            raise RuntimeError("existing_observer_ledger_policy_mismatch")
        raw_existing = existing.get("observations", [])
        if not isinstance(raw_existing, list):
            raise RuntimeError("existing_observer_ledger_observations_invalid")
        current = [dict(item) for item in raw_existing if isinstance(item, Mapping)]
        if len(current) != len(raw_existing):
            raise RuntimeError("existing_observer_ledger_observation_not_mapping")

    by_signal: dict[str, dict[str, Any]] = {}
    by_decision: dict[str, str] = {}
    for item in current:
        signal_id = str(item.get("signal_id") or "").strip()
        decision_event_id = str(item.get("decision_event_id") or "").strip()
        observation_sha = str(item.get("observation_sha256") or "").strip()
        if not signal_id or not decision_event_id or not observation_sha:
            raise RuntimeError("existing_observer_ledger_identity_incomplete")
        _require_utc_datetime(
            item.get("ledger_recorded_at_utc"),
            "existing_ledger_recorded_at_utc",
        )
        if signal_id in by_signal:
            raise RuntimeError(f"existing_observer_ledger_duplicate_signal_id:{signal_id}")
        if decision_event_id in by_decision:
            raise RuntimeError(
                f"existing_observer_ledger_duplicate_decision_event_id:{decision_event_id}"
            )
        by_signal[signal_id] = item
        by_decision[decision_event_id] = signal_id

    raw_new = report.get("observations", [])
    if not isinstance(raw_new, list):
        raise RuntimeError("observer_report_observations_invalid")

    new_count = 0
    idempotent_count = 0
    for raw in raw_new:
        if not isinstance(raw, Mapping):
            raise RuntimeError("observer_report_observation_not_mapping")
        item = dict(raw)
        signal_id = str(item.get("signal_id") or "").strip()
        decision_event_id = str(item.get("decision_event_id") or "").strip()
        observation_sha = str(item.get("observation_sha256") or "").strip()
        if not signal_id or not decision_event_id or not observation_sha:
            raise RuntimeError("observer_report_observation_identity_incomplete")
        if item.get("ledger_recorded_at_utc") is not None:
            raise RuntimeError("observer_report_must_not_prepopulate_ledger_recorded_at")

        existing_signal = by_signal.get(signal_id)
        if existing_signal is not None:
            if existing_signal.get("observation_sha256") != observation_sha:
                raise RuntimeError(f"observer_signal_identity_mutation:{signal_id}")
            idempotent_count += 1
            continue

        prior_signal = by_decision.get(decision_event_id)
        if prior_signal is not None and prior_signal != signal_id:
            raise RuntimeError(
                "observer_decision_event_identity_collision:"
                f"{decision_event_id}:{prior_signal}:{signal_id}"
            )

        score_completed = _require_utc_datetime(
            item.get("score_completed_at_utc"),
            "score_completed_at_utc",
        )
        valid_until = _require_utc_datetime(
            item.get("signal_valid_until_utc"),
            "signal_valid_until_utc",
        )
        if recorded_at < score_completed:
            raise RuntimeError(
                f"observer_ledger_recorded_before_score_completion:{signal_id}"
            )
        if recorded_at > valid_until:
            raise RuntimeError(
                f"observer_ledger_recorded_after_signal_expiry:{signal_id}"
            )
        item["ledger_recorded_at_utc"] = recorded_at.isoformat()
        by_signal[signal_id] = item
        by_decision[decision_event_id] = signal_id
        new_count += 1

    observations = sorted(
        by_signal.values(),
        key=lambda item: (
            str(item.get("decision_timestamp_utc") or ""),
            str(item.get("signal_id") or ""),
        ),
    )
    ledger_hash_payload = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "policy_sha256": policy_sha,
        "prospective_start_utc": report.get("prospective_start_utc"),
        "identity_authority": "sealed_decision_ledger_v4_2",
        "observations": observations,
    }
    ledger_sha = _sha256_json(ledger_hash_payload)
    return {
        **ledger_hash_payload,
        "observer_schema_version": SCHEMA_VERSION,
        "observation_count": len(observations),
        "selected_signal_count": sum(
            1 for item in observations if item.get("selected") is True
        ),
        "new_observation_count": new_count,
        "idempotent_observation_count": idempotent_count,
        "ledger_sha256": ledger_sha,
        "updated_at_utc": recorded_at.isoformat(),
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "changes_risk": False,
        "sends_orders": False,
        "exchange_private_access": False,
    }


def _require_utc_datetime(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise RuntimeError(f"{field}_missing")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{field}_must_be_timezone_aware")
    if parsed.utcoffset().total_seconds() != 0:
        raise RuntimeError(f"{field}_must_use_utc_offset_zero")
    return parsed.astimezone(UTC)


def _validate_ledger_path(root: Path, path: Path) -> None:
    allowed_root = (root / "data" / "research" / "qlib_v2").resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise RuntimeError(f"observer_ledger_path_outside_research_root:{candidate}") from exc
    if candidate.suffix.lower() != ".json":
        raise RuntimeError("observer_ledger_path_must_be_json")


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root).resolve()
    ledger_path = _resolve(root, args.ledger)
    if args.write_ledger:
        _validate_ledger_path(root, ledger_path)
    report = build_qlib_v2_prospective_signal_observer_v1(
        project_root=root,
        freeze_spec_path=args.freeze_spec,
        active_signals_path=args.active_signals,
        decision_ledger_path=args.decision_ledger,
        outcome_path=args.outcome_path,
        market_features_path=args.market_features_path,
    )

    write_performed = False
    ledger_payload: dict[str, Any] | None = None
    if args.write_ledger:
        if report.get("status") == "blocked":
            raise RuntimeError(
                f"observer_write_blocked:{report.get('reason', 'unknown_reason')}"
            )
        if int(report.get("observation_count") or 0) <= 0:
            raise RuntimeError("observer_write_blocked:no_new_prospective_observations")
        existing = _read_json_object(ledger_path)
        ledger_payload = _merge_ledger(existing=existing, report=report)
        _write_json_atomic(ledger_path, ledger_payload)
        write_performed = True

    output = dict(report)
    output["write_requested"] = bool(args.write_ledger)
    output["write_performed"] = write_performed
    output["writes_runtime"] = False
    output["writes_research_ledger"] = write_performed
    output["ledger_path"] = str(ledger_path)
    if ledger_payload is not None:
        output["ledger_observation_count"] = ledger_payload["observation_count"]
        output["ledger_new_observation_count"] = ledger_payload[
            "new_observation_count"
        ]
        output["ledger_idempotent_observation_count"] = ledger_payload[
            "idempotent_observation_count"
        ]
        output["ledger_sha256"] = ledger_payload["ledger_sha256"]
    return output


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except Exception as exc:
        report = {
            "status": "blocked",
            "reason": str(exc).splitlines()[0][:500],
            "error_type": type(exc).__name__,
            "decision": "MANTER_EM_RESEARCH",
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "promotion_allowed": False,
            "changes_risk": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "writes_runtime": False,
            "writes_research_ledger": False,
            "write_performed": False,
        }
    if args.json:
        print(json.dumps(report, sort_keys=True, default=str))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"observations={report.get('observation_count', 0)} "
            f"selected={report.get('selected_signal_count', 0)}"
        )
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
