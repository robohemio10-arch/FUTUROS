"""Research-only replay for OCR Master candle shadow observations.

The replay applies survivor observation contracts hypothetically to closed
historical rows. It does not apply rules, write runtime state, update models or
emit signals.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from smartcrypto.research.paper_closed_trades_readonly_source_contract.source_contract import (
    load_closed_trade_source_candidates,
    normalize_closed_trade_rows,
)


SCHEMA_VERSION = "ocr_master_candle_shadow_observation_replay_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/ocr_master_candle_shadow_observation_replay_v1.json")

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "read_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "paper_observation_allowed": False,
    "ready_for_shadow_observation": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "registers_shadow_rules": False,
    "applies_shadow_rules": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "exchange_private_access": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}

FORBIDDEN_ACTIONS = [
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar shadow rule",
    "aplicar shadow rule",
    "alterar modelos",
    "alterar sinais ativos",
    "alterar configuracoes",
    "enviar ordem real",
    "usar exchange privada",
    "usar replay como permissao ou veto operacional",
]

REQUIRED_REPLAY_FIELDS = [
    "replay_trade_count",
    "would_allow_count",
    "would_block_count",
    "would_allow_net_pnl",
    "would_allow_profit_factor",
    "would_allow_win_rate",
    "baseline_net_pnl",
    "baseline_profit_factor",
    "baseline_win_rate",
    "expected_value_delta_total",
    "expected_value_delta_mean",
    "missed_opportunity_count",
    "preserved_loss_count",
    "false_positive_observation_count",
    "survivor_attribution_table",
]

PNL_COLUMNS = (
    "net_pnl",
    "pnl",
    "pnl_usdt",
    "profit_abs",
    "reported_pnl_usdt",
    "pnl_fechado",
    "realized_pnl",
)


@dataclass(frozen=True)
class LoadedReplayInputs:
    survivors: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    input_mode: str
    source_status: str
    source_reason: str
    survivor_source_path: str | None = None
    survivor_source_sha256: str | None = None
    trades_source_path: str | None = None
    trades_source_sha256: str | None = None
    closed_trades_source_contract_path: str | None = None
    closed_trades_source_contract_sha256: str | None = None
    closed_trades_contract_join_key: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_float(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric) or math.isinf(numeric):
        return default
    return numeric


def _round(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), 10)


def _normalize_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace("-", "").strip()


def _normalize_side(value: object) -> str:
    text = str(value or "").lower().strip()
    if text in {"buy", "long", "comprado"}:
        return "long"
    if text in {"sell", "short", "vendido"}:
        return "short"
    return text


def _trade_value(trade: Mapping[str, Any], dimension: str) -> str:
    dimension_key = str(dimension)
    raw_key = dimension_key.removesuffix("_norm")
    if dimension_key in {"symbol_norm", "symbol", "moeda"}:
        return _normalize_symbol(trade.get("symbol") or trade.get("moeda") or trade.get("pair"))
    if dimension_key in {"side_norm", "side", "fechar_side"}:
        return _normalize_side(trade.get("side") or trade.get("fechar_side"))
    return str(trade.get(dimension_key, trade.get(raw_key, ""))).strip()


def _trade_pnl(trade: Mapping[str, Any]) -> float:
    for column in PNL_COLUMNS:
        if column in trade:
            return _safe_float(trade.get(column))
    return 0.0


def _profit_factor(values: Sequence[float]) -> float | None:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss == 0:
        return None if gross_profit == 0 else None
    return _round(gross_profit / gross_loss)


def _win_rate(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return _round(sum(1 for value in values if value > 0) / len(values))


def survivor_matches_trade(survivor: Mapping[str, Any], trade: Mapping[str, Any]) -> bool:
    """Return whether a closed historical trade belongs to a survivor cohort."""

    dimensions = survivor.get("dimensions")
    values = survivor.get("values")
    if isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)) and isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        pairs = list(zip(dimensions, values, strict=False))
        if pairs:
            return all(_trade_value(trade, str(dimension)) == str(value).strip() for dimension, value in pairs)

    conditions = survivor.get("conditions")
    if isinstance(conditions, Sequence) and not isinstance(conditions, (str, bytes)):
        return all(_condition_matches(str(condition), trade) for condition in conditions)
    return False


def _condition_matches(condition: str, trade: Mapping[str, Any]) -> bool:
    if condition.startswith("symbol_"):
        return _normalize_symbol(trade.get("symbol") or trade.get("moeda") or trade.get("pair")) == condition.removeprefix("symbol_")
    if condition.startswith("side_"):
        return _normalize_side(trade.get("side") or trade.get("fechar_side")) == condition.removeprefix("side_")
    if "==" in condition:
        key, raw_expected = condition.split("==", 1)
        expected = raw_expected.strip().strip("'\"")
        return _trade_value(trade, key.strip()) == expected
    return False


def replay_survivors_on_trades(
    survivors: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize hypothetical would_allow/would_block observations."""

    rows: list[dict[str, Any]] = []
    sorted_survivors = sorted(
        [dict(item) for item in survivors],
        key=lambda item: str(item.get("survivor_rule_id") or item.get("candidate_id") or ""),
    )
    for index, trade in enumerate(trades, start=1):
        matches = [survivor for survivor in sorted_survivors if survivor_matches_trade(survivor, trade)]
        selected = matches[0] if matches else None
        pnl = _trade_pnl(trade)
        would_allow = selected is not None
        rows.append(
            {
                "replay_row_id": f"replay_{index:06d}",
                "trade_id": str(trade.get("trade_id") or trade.get("id") or f"trade_{index}"),
                "order_id": trade.get("order_id"),
                "internal_order_id": trade.get("internal_order_id"),
                "row_fingerprint": trade.get("row_fingerprint"),
                "symbol": _normalize_symbol(trade.get("symbol") or trade.get("moeda") or trade.get("pair")),
                "side": _normalize_side(trade.get("side") or trade.get("fechar_side")),
                "open_time": trade.get("open_time") or trade.get("open_time_utc") or trade.get("horario_abertura"),
                "close_time": trade.get("close_time") or trade.get("close_time_utc") or trade.get("horario_fechamento"),
                "pnl": _round(pnl),
                "would_allow": would_allow,
                "would_block": not would_allow,
                "matched_survivor_rule_id": None if selected is None else str(selected.get("survivor_rule_id") or selected.get("candidate_id") or ""),
                "matched_survivor_expression": None if selected is None else str(selected.get("survivor_expression") or selected.get("expression") or ""),
                "expected_value_delta": None if selected is None else _round(_safe_float(selected.get("expected_value_delta"))),
                "research_only": True,
                "operational_action_allowed": False,
                "can_be_used_as_signal": False,
                "can_be_used_as_veto": False,
            }
        )
    return rows


def compute_replay_metrics(
    survivors: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute deterministic attribution metrics for survivor replay."""

    replay_rows = replay_survivors_on_trades(survivors, trades)
    all_pnl = [_safe_float(row.get("pnl")) for row in replay_rows]
    allow_rows = [row for row in replay_rows if row["would_allow"]]
    block_rows = [row for row in replay_rows if row["would_block"]]
    allow_pnl = [_safe_float(row.get("pnl")) for row in allow_rows]
    ev_values = [_safe_float(row.get("expected_value_delta")) for row in allow_rows if row.get("expected_value_delta") is not None]

    attribution_table = []
    for survivor in sorted(survivors, key=lambda item: str(item.get("survivor_rule_id") or item.get("candidate_id") or "")):
        survivor_id = str(survivor.get("survivor_rule_id") or survivor.get("candidate_id") or "")
        matched = [row for row in replay_rows if row.get("matched_survivor_rule_id") == survivor_id]
        matched_pnl = [_safe_float(row.get("pnl")) for row in matched]
        survivor_ev = _safe_float(survivor.get("expected_value_delta"))
        attribution_table.append(
            {
                "survivor_rule_id": survivor_id,
                "survivor_expression": str(survivor.get("survivor_expression") or survivor.get("expression") or ""),
                "would_allow_count": len(matched),
                "would_allow_net_pnl": _round(sum(matched_pnl)),
                "would_allow_profit_factor": _profit_factor(matched_pnl),
                "would_allow_win_rate": _win_rate(matched_pnl),
                "expected_value_delta_total": _round(survivor_ev * len(matched)),
                "expected_value_delta_mean": _round(survivor_ev if matched else 0.0),
                "false_positive_observation_count": sum(1 for value in matched_pnl if value < 0),
                "research_only": True,
                "operational_action_allowed": False,
            }
        )

    metrics = {
        "replay_trade_count": len(replay_rows),
        "would_allow_count": len(allow_rows),
        "would_block_count": len(block_rows),
        "would_allow_net_pnl": _round(sum(allow_pnl)),
        "would_allow_profit_factor": _profit_factor(allow_pnl),
        "would_allow_win_rate": _win_rate(allow_pnl),
        "baseline_net_pnl": _round(sum(all_pnl)),
        "baseline_profit_factor": _profit_factor(all_pnl),
        "baseline_win_rate": _win_rate(all_pnl),
        "expected_value_delta_total": _round(sum(ev_values)),
        "expected_value_delta_mean": _round(sum(ev_values) / len(ev_values)) if ev_values else None,
        "missed_opportunity_count": sum(1 for row in block_rows if _safe_float(row.get("pnl")) > 0),
        "preserved_loss_count": sum(1 for row in block_rows if _safe_float(row.get("pnl")) < 0),
        "false_positive_observation_count": sum(1 for row in allow_rows if _safe_float(row.get("pnl")) < 0),
        "survivor_attribution_table": attribution_table,
        "replay_rows": replay_rows,
        "replay_rows_sample": replay_rows[:20],
    }
    return metrics


def _extract_survivors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("observation_records", "survivors", "oos_shortlist", "survivor_rules"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _extract_trades(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("trades", "closed_trades", "rows", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_trades_source(path: Path) -> tuple[list[dict[str, Any]], str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _extract_trades(_read_json(path)), "json"
    if suffix == ".csv":
        return _read_csv(path), "csv"
    return [], f"unsupported_trades_source:{suffix or 'no_suffix'}"


def _read_closed_trades_from_contract(
    *,
    project_root: Path,
    contract_path: Path,
) -> tuple[list[dict[str, Any]], str | None, str | None, str]:
    payload = _read_json(contract_path)
    if not isinstance(payload, Mapping):
        return [], None, None, "closed_trades_source_contract_invalid_payload"
    if payload.get("source_contract_status") != "ok":
        return [], None, None, "closed_trades_source_contract_not_ok"
    recommended_join_key = payload.get("recommended_join_key")
    selected_source = payload.get("selected_source_path")
    normalized_sample = payload.get("normalized_rows_sample")
    normalized_count = int(_safe_float(payload.get("normalized_closed_trade_count")))
    if isinstance(normalized_sample, list) and normalized_count == len(normalized_sample):
        return [dict(item) for item in normalized_sample if isinstance(item, Mapping)], None, str(recommended_join_key or ""), "closed_trades_loaded_from_contract_rows"
    if not selected_source:
        return [], None, str(recommended_join_key or ""), "closed_trades_source_contract_missing_selected_source_path"
    selected_path = Path(str(selected_source))
    if not selected_path.is_absolute():
        selected_path = project_root / selected_path
    loaded = load_closed_trade_source_candidates(
        project_root=project_root,
        allow_runtime_read=True,
        source_paths=[selected_path],
    )
    selected = next((candidate for candidate in loaded.candidates if candidate.status == "ok" and candidate.rows), None)
    if selected is None:
        return [], _project_relative(selected_path, project_root), str(recommended_join_key or ""), loaded.source_reason
    normalized, _rejected, _mapping = normalize_closed_trade_rows(
        selected.rows,
        source_path=_project_relative(selected.path, project_root),
        source_sha256=selected.sha256,
    )
    return normalized, _project_relative(selected.path, project_root), str(recommended_join_key or ""), (
        "closed_trades_loaded_from_contract_selected_source" if normalized else "closed_trades_source_contract_no_normalized_rows"
    )


def load_replay_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    observation_design_report: str | Path | None = None,
    oos_report: str | Path | None = None,
    trades_master: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    survivor_records: Sequence[Mapping[str, Any]] | None = None,
    closed_trades: Sequence[Mapping[str, Any]] | None = None,
) -> LoadedReplayInputs:
    """Load replay inputs only when explicitly supplied or allowed."""

    root = Path(project_root).resolve()
    if survivor_records is not None or closed_trades is not None:
        return LoadedReplayInputs(
            survivors=[dict(item) for item in survivor_records or []],
            trades=[dict(item) for item in closed_trades or []],
            input_mode="in_memory_replay_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
        )
    if not allow_runtime_read:
        return LoadedReplayInputs(
            survivors=[],
            trades=[],
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
        )

    survivor_path_raw = observation_design_report or oos_report
    if survivor_path_raw is None or (trades_master is None and closed_trades_source_contract is None):
        return LoadedReplayInputs(
            survivors=[],
            trades=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_required_sources",
        )

    survivor_path = Path(survivor_path_raw)
    trade_path = Path(trades_master) if trades_master is not None else None
    contract_path = Path(closed_trades_source_contract) if closed_trades_source_contract is not None else None
    if not survivor_path.is_absolute():
        survivor_path = root / survivor_path
    if trade_path is not None and not trade_path.is_absolute():
        trade_path = root / trade_path
    if contract_path is not None and not contract_path.is_absolute():
        contract_path = root / contract_path
    if not survivor_path.exists() or (trade_path is not None and not trade_path.exists()) or (contract_path is not None and not contract_path.exists()):
        return LoadedReplayInputs(
            survivors=[],
            trades=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="source_path_missing",
            survivor_source_path=_project_relative(survivor_path, root),
            trades_source_path=None if trade_path is None else _project_relative(trade_path, root),
            closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
        )

    try:
        survivors = _extract_survivors(_read_json(survivor_path))
        if contract_path is not None:
            trades, selected_source_path, recommended_join_key, trade_source_mode = _read_closed_trades_from_contract(
                project_root=root,
                contract_path=contract_path,
            )
            trade_source_path = selected_source_path
            trade_source_sha256 = None
            contract_sha256 = _sha256_file(contract_path)
        else:
            assert trade_path is not None
            trades, trade_source_mode = _read_trades_source(trade_path)
            trade_source_path = _project_relative(trade_path, root)
            trade_source_sha256 = _sha256_file(trade_path)
            contract_sha256 = None
            recommended_join_key = None
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        return LoadedReplayInputs(
            survivors=[],
            trades=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            survivor_source_path=_project_relative(survivor_path, root),
            survivor_source_sha256=_sha256_file(survivor_path),
            trades_source_path=None if trade_path is None else _project_relative(trade_path, root),
            trades_source_sha256=None if trade_path is None else _sha256_file(trade_path),
            closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
            closed_trades_source_contract_sha256=None if contract_path is None else _sha256_file(contract_path),
        )
    if trade_source_mode.startswith("unsupported_trades_source"):
        return LoadedReplayInputs(
            survivors=survivors,
            trades=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=trade_source_mode,
            survivor_source_path=_project_relative(survivor_path, root),
            survivor_source_sha256=_sha256_file(survivor_path),
            trades_source_path=None if trade_path is None else _project_relative(trade_path, root),
            trades_source_sha256=None if trade_path is None else _sha256_file(trade_path),
        )
    return LoadedReplayInputs(
        survivors=survivors,
        trades=trades,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        survivor_source_path=_project_relative(survivor_path, root),
        survivor_source_sha256=_sha256_file(survivor_path),
        trades_source_path=trade_source_path,
        trades_source_sha256=trade_source_sha256,
        closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
        closed_trades_source_contract_sha256=contract_sha256,
        closed_trades_contract_join_key=recommended_join_key,
    )


def _reason(inputs: LoadedReplayInputs) -> str:
    if inputs.source_status != "ok":
        if inputs.input_mode == "no_runtime_rows_loaded":
            return "shadow_observation_replay_requires_explicit_runtime_read_or_in_memory_inputs"
        return inputs.source_reason
    if not inputs.survivors:
        return "shadow_observation_replay_blocked_no_survivors"
    if not inputs.trades:
        return "shadow_observation_replay_blocked_no_closed_trades"
    return "shadow_observation_replay_completed_research_only_no_operational_authority"


def build_shadow_observation_replay_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    observation_design_report: str | Path | None = None,
    oos_report: str | Path | None = None,
    trades_master: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    survivor_records: Sequence[Mapping[str, Any]] | None = None,
    closed_trades: Sequence[Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only observation replay report."""

    root = Path(project_root).resolve()
    inputs = load_replay_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        observation_design_report=observation_design_report,
        oos_report=oos_report,
        trades_master=trades_master,
        closed_trades_source_contract=closed_trades_source_contract,
        survivor_records=survivor_records,
        closed_trades=closed_trades,
    )
    write_requested = bool(write and not no_write)
    can_replay = inputs.source_status == "ok" and bool(inputs.survivors) and bool(inputs.trades)
    metrics = compute_replay_metrics(inputs.survivors, inputs.trades) if can_replay else _empty_metrics()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": _reason(inputs),
        "decision": DECISION_RESEARCH,
        "input_mode": inputs.input_mode,
        "source_status": inputs.source_status,
        "source_reason": inputs.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "survivor_source_path": inputs.survivor_source_path,
        "survivor_source_sha256": inputs.survivor_source_sha256,
        "trades_source_path": inputs.trades_source_path,
        "trades_source_sha256": inputs.trades_source_sha256,
        "closed_trades_source_contract_path": inputs.closed_trades_source_contract_path,
        "closed_trades_source_contract_sha256": inputs.closed_trades_source_contract_sha256,
        "closed_trades_contract_join_key": inputs.closed_trades_contract_join_key,
        "survivor_count": len(inputs.survivors),
        "closed_trade_count": len(inputs.trades),
        "replay_semantics": {
            "would_allow": "historical row belonged to the research-only observation cohort",
            "would_block": "historical row stayed outside the research-only observation cohort",
            "operational_use": "forbidden: not a signal, not an order permission, not a runtime veto",
        },
        "replay_metrics": metrics,
        "required_replay_fields": list(REQUIRED_REPLAY_FIELDS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_replay_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report)
        output_error = _validate_output_report_path(root, output_path)
        if output_error is not None:
            report["reason"] = output_error
            report["validation_errors"] = validate_replay_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
    return report


def validate_replay_report(report: Mapping[str, Any]) -> list[str]:
    """Validate the non-operational replay contract."""

    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if report.get("status") != "blocked":
        errors.append("status_must_remain_blocked")
    if report.get("decision") != DECISION_RESEARCH:
        errors.append("decision_must_remain_research")
    for key, expected in SAFETY_FLAGS.items():
        if report.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")
    metrics = report.get("replay_metrics")
    if not isinstance(metrics, Mapping):
        errors.append("replay_metrics_missing")
    else:
        for field in REQUIRED_REPLAY_FIELDS:
            if field not in metrics:
                errors.append(f"missing_replay_metric:{field}")
    return errors


def _empty_metrics() -> dict[str, Any]:
    return {
        "replay_trade_count": 0,
        "would_allow_count": 0,
        "would_block_count": 0,
        "would_allow_net_pnl": 0.0,
        "would_allow_profit_factor": None,
        "would_allow_win_rate": None,
        "baseline_net_pnl": 0.0,
        "baseline_profit_factor": None,
        "baseline_win_rate": None,
        "expected_value_delta_total": 0.0,
        "expected_value_delta_mean": None,
        "missed_opportunity_count": 0,
        "preserved_loss_count": 0,
        "false_positive_observation_count": 0,
        "survivor_attribution_table": [],
        "replay_rows_sample": [],
    }


def _resolve_output_report(root: Path, output_report: str | Path | None) -> Path:
    path = Path(output_report) if output_report is not None else DEFAULT_OUTPUT_REPORT
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _validate_output_report_path(root: Path, path: Path) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != ".json":
        return "write_blocked_output_must_be_json_report"
    return None
