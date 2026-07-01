"""Research-only attribution of closed paper trades to shadow replay rows.

This module measures hypothetical attribution between already closed paper
trades and shadow observation replay evidence. It never applies rules, registers
survivors, changes runtime state, updates models or emits signals.
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

from smartcrypto.research.ocr_master_candle_shadow_observation_replay.replay import (
    survivor_matches_trade,
)
from smartcrypto.research.paper_closed_trades_readonly_source_contract.source_contract import (
    load_closed_trade_source_candidates,
    normalize_closed_trade_rows,
)


SCHEMA_VERSION = "paper_closed_trades_shadow_rule_attribution_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_closed_trades_shadow_rule_attribution_v1.json")

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
    "registrar shadow rule",
    "aplicar shadow rule",
    "usar atribuicao como sinal operacional",
    "usar atribuicao como veto runtime",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar registry",
    "alterar sinais ativos",
    "enviar ordem",
    "acessar exchange privada",
]

PNL_COLUMNS = (
    "net_pnl",
    "pnl",
    "pnl_usdt",
    "profit_abs",
    "reported_pnl_usdt",
    "pnl_fechado",
    "realized_pnl",
    "raw_pnl_usdt",
    "shadow_filtered_pnl_usdt",
)

REQUIRED_TOP_LEVEL_FIELDS = [
    "status",
    "reason",
    "decision",
    "input_mode",
    "closed_trade_count",
    "attributed_trade_count",
    "unattributed_trade_count",
    "would_allow_count",
    "would_block_count",
    "missed_opportunity_count",
    "preserved_loss_count",
    "false_positive_observation_count",
    "expected_value_delta_total",
    "expected_value_delta_mean",
    "attribution_table_sample",
    "survivor_attribution_summary",
    "gate_summary",
    "safety_flags",
    "write_performed",
]


@dataclass(frozen=True)
class LoadedAttributionInputs:
    closed_trades: list[dict[str, Any]]
    replay_rows: list[dict[str, Any]]
    survivors: list[dict[str, Any]]
    input_mode: str
    source_status: str
    source_reason: str
    closed_trades_source_path: str | None = None
    closed_trades_source_sha256: str | None = None
    shadow_replay_source_path: str | None = None
    shadow_replay_source_sha256: str | None = None
    closed_trades_source_contract_path: str | None = None
    closed_trades_source_contract_sha256: str | None = None
    recommended_join_key: str | None = None


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


def _safe_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
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


def _trade_id(row: Mapping[str, Any], fallback_index: int | None = None) -> str:
    raw = row.get("trade_id") or row.get("id") or row.get("order_id")
    if raw is None or str(raw).strip() == "":
        return f"trade_{fallback_index}" if fallback_index is not None else ""
    text = str(raw).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _join_value(row: Mapping[str, Any], join_key: str, fallback_index: int | None = None) -> str:
    if join_key == "trade_id":
        return _trade_id(row, fallback_index)
    raw = row.get(join_key)
    if raw is None or str(raw).strip() == "":
        return _trade_id(row, fallback_index)
    text = str(raw).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _trade_pnl(row: Mapping[str, Any]) -> float:
    for column in PNL_COLUMNS:
        if column in row:
            return _safe_float(row.get(column))
    return 0.0


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim", "y"}


def _profit_factor(values: Sequence[float]) -> float | None:
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    if gross_loss == 0:
        return None
    return _round(gross_profit / gross_loss)


def _win_rate(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return _round(sum(1 for value in values if value > 0) / len(values))


def _extract_rows(payload: Any, keys: Sequence[str]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
            if isinstance(value, Mapping):
                nested = _extract_rows(value, keys)
                if nested:
                    return nested
    return []


def _extract_closed_trades(payload: Any) -> list[dict[str, Any]]:
    return _extract_rows(payload, ("closed_trades", "trades", "rows", "records", "data"))


def _extract_replay_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("replay_rows", "attribution_rows", "replay_rows_sample"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
        metrics = payload.get("replay_metrics")
        if isinstance(metrics, Mapping):
            return _extract_replay_rows(metrics)
    return _extract_rows(payload, ("replay_rows", "attribution_rows", "rows", "records", "data"))


def _extract_survivors(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("survivor_attribution_table", "observation_records", "survivors", "oos_shortlist", "survivor_rules"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
        metrics = payload.get("replay_metrics")
        if isinstance(metrics, Mapping):
            return _extract_survivors(metrics)
    return []


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_closed_trades_source(path: Path) -> tuple[list[dict[str, Any]], str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _extract_closed_trades(_read_json(path)), "json"
    if suffix == ".csv":
        return _read_csv(path), "csv"
    return [], f"unsupported_closed_trades_source:{suffix or 'no_suffix'}"


def _read_replay_source(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    suffix = path.suffix.lower()
    if suffix != ".json":
        return [], [], f"unsupported_shadow_replay_source:{suffix or 'no_suffix'}"
    payload = _read_json(path)
    return _extract_replay_rows(payload), _extract_survivors(payload), "json"


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


def _resolve_path(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def load_attribution_inputs(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    closed_trades_path: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    observation_design_report: str | Path | None = None,
    oos_report: str | Path | None = None,
    closed_trades: Sequence[Mapping[str, Any]] | None = None,
    replay_rows: Sequence[Mapping[str, Any]] | None = None,
    survivor_records: Sequence[Mapping[str, Any]] | None = None,
) -> LoadedAttributionInputs:
    """Load attribution inputs only from memory or explicit read-only sources."""

    root = Path(project_root).resolve()
    if closed_trades is not None or replay_rows is not None or survivor_records is not None:
        return LoadedAttributionInputs(
            closed_trades=[dict(item) for item in closed_trades or []],
            replay_rows=[dict(item) for item in replay_rows or []],
            survivors=[dict(item) for item in survivor_records or []],
            input_mode="in_memory_attribution_inputs",
            source_status="ok",
            source_reason="in_memory_inputs_supplied",
        )
    if not allow_runtime_read:
        return LoadedAttributionInputs(
            closed_trades=[],
            replay_rows=[],
            survivors=[],
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
        )

    replay_source_raw = shadow_replay_report or observation_design_report or oos_report
    closed_path = _resolve_path(root, closed_trades_path)
    replay_path = _resolve_path(root, replay_source_raw)
    contract_path = _resolve_path(root, closed_trades_source_contract)
    if (closed_path is None and contract_path is None) or replay_path is None:
        return LoadedAttributionInputs(
            closed_trades=[],
            replay_rows=[],
            survivors=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="missing_required_sources",
        )
    if (closed_path is not None and not closed_path.exists()) or (contract_path is not None and not contract_path.exists()) or not replay_path.exists():
        return LoadedAttributionInputs(
            closed_trades=[],
            replay_rows=[],
            survivors=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason="source_path_missing",
            closed_trades_source_path=None if closed_path is None else _project_relative(closed_path, root),
            closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
            shadow_replay_source_path=_project_relative(replay_path, root),
        )

    try:
        if contract_path is not None:
            closed_rows, selected_source_path, recommended_join_key, closed_mode = _read_closed_trades_from_contract(
                project_root=root,
                contract_path=contract_path,
            )
            closed_path_for_report = selected_source_path
            closed_sha_for_report = None
            contract_sha = _sha256_file(contract_path)
        else:
            assert closed_path is not None
            closed_rows, closed_mode = _read_closed_trades_source(closed_path)
            closed_path_for_report = _project_relative(closed_path, root)
            closed_sha_for_report = _sha256_file(closed_path)
            contract_sha = None
            recommended_join_key = None
        replay_rows_loaded, survivors_loaded, replay_mode = _read_replay_source(replay_path)
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        return LoadedAttributionInputs(
            closed_trades=[],
            replay_rows=[],
            survivors=[],
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=f"source_read_failed:{type(exc).__name__}",
            closed_trades_source_path=None if closed_path is None else _project_relative(closed_path, root),
            closed_trades_source_sha256=None if closed_path is None else _sha256_file(closed_path),
            closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
            closed_trades_source_contract_sha256=None if contract_path is None else _sha256_file(contract_path),
            shadow_replay_source_path=_project_relative(replay_path, root),
            shadow_replay_source_sha256=_sha256_file(replay_path),
        )
    if closed_mode.startswith("unsupported_closed_trades_source") or replay_mode.startswith("unsupported_shadow_replay_source"):
        return LoadedAttributionInputs(
            closed_trades=closed_rows,
            replay_rows=replay_rows_loaded,
            survivors=survivors_loaded,
            input_mode="runtime_read_requested",
            source_status="blocked",
            source_reason=closed_mode if closed_mode.startswith("unsupported") else replay_mode,
            closed_trades_source_path=None if closed_path is None else _project_relative(closed_path, root),
            closed_trades_source_sha256=None if closed_path is None else _sha256_file(closed_path),
            shadow_replay_source_path=_project_relative(replay_path, root),
            shadow_replay_source_sha256=_sha256_file(replay_path),
            closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
            closed_trades_source_contract_sha256=None if contract_path is None else _sha256_file(contract_path),
            recommended_join_key=recommended_join_key,
        )
    return LoadedAttributionInputs(
        closed_trades=closed_rows,
        replay_rows=replay_rows_loaded,
        survivors=survivors_loaded,
        input_mode="runtime_read_requested",
        source_status="ok",
        source_reason="sources_loaded_read_only",
        closed_trades_source_path=closed_path_for_report,
        closed_trades_source_sha256=closed_sha_for_report,
        shadow_replay_source_path=_project_relative(replay_path, root),
        shadow_replay_source_sha256=_sha256_file(replay_path),
        closed_trades_source_contract_path=None if contract_path is None else _project_relative(contract_path, root),
        closed_trades_source_contract_sha256=contract_sha,
        recommended_join_key=recommended_join_key,
    )


def _replay_rows_by_join_key(replay_rows: Sequence[Mapping[str, Any]], join_key: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(replay_rows, start=1):
        value = _join_value(row, join_key, index)
        if value and value not in indexed:
            indexed[value] = row
    return indexed


def _survivor_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("matched_survivor_rule_id") or row.get("survivor_rule_id") or row.get("candidate_id")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _survivor_expression(row: Mapping[str, Any]) -> str | None:
    value = row.get("matched_survivor_expression") or row.get("survivor_expression") or row.get("expression")
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _find_survivor_match(survivors: Sequence[Mapping[str, Any]], trade: Mapping[str, Any]) -> Mapping[str, Any] | None:
    sorted_survivors = sorted(
        [dict(item) for item in survivors],
        key=lambda item: str(item.get("survivor_rule_id") or item.get("candidate_id") or ""),
    )
    for survivor in sorted_survivors:
        if survivor_matches_trade(survivor, trade):
            return survivor
    return None


def attribute_closed_trades_to_shadow_replay(
    closed_trades: Sequence[Mapping[str, Any]],
    replay_rows: Sequence[Mapping[str, Any]],
    survivor_records: Sequence[Mapping[str, Any]] | None = None,
    *,
    join_key: str = "trade_id",
) -> list[dict[str, Any]]:
    """Materialize deterministic research-only attribution rows."""

    replay_by_join_key = _replay_rows_by_join_key(replay_rows, join_key)
    rows: list[dict[str, Any]] = []
    for index, trade in enumerate(closed_trades, start=1):
        trade_id = _trade_id(trade, index)
        replay = replay_by_join_key.get(_join_value(trade, join_key, index))
        survivor = None if replay is not None else _find_survivor_match(survivor_records or [], trade)
        pnl = _trade_pnl(trade)
        attributed = replay is not None or survivor is not None
        if replay is not None:
            would_allow = _truthy(replay.get("would_allow"))
            would_block = _truthy(replay.get("would_block")) or not would_allow
            survivor_id = _survivor_id(replay)
            survivor_expression = _survivor_expression(replay)
            ev_delta = _safe_optional_float(replay.get("expected_value_delta"))
            attribution_method = "shadow_replay_trade_id"
        elif survivor is not None:
            would_allow = True
            would_block = False
            survivor_id = _survivor_id(survivor)
            survivor_expression = _survivor_expression(survivor)
            ev_delta = _safe_optional_float(survivor.get("expected_value_delta"))
            attribution_method = "survivor_contract_match"
        else:
            would_allow = False
            would_block = False
            survivor_id = None
            survivor_expression = None
            ev_delta = None
            attribution_method = "unattributed"

        rows.append(
            {
                "attribution_row_id": f"paper_shadow_attr_{index:06d}",
                "trade_id": trade_id,
                "order_id": trade.get("order_id"),
                "internal_order_id": trade.get("internal_order_id"),
                "row_fingerprint": trade.get("row_fingerprint"),
                "join_key": join_key,
                "join_value": _join_value(trade, join_key, index),
                "symbol": _normalize_symbol(trade.get("symbol") or trade.get("moeda") or trade.get("pair")),
                "side": _normalize_side(trade.get("side") or trade.get("fechar_side")),
                "pnl": _round(pnl),
                "attributed": attributed,
                "attribution_method": attribution_method,
                "would_allow": would_allow,
                "would_block": would_block,
                "matched_survivor_rule_id": survivor_id,
                "matched_survivor_expression": survivor_expression,
                "expected_value_delta": _round(ev_delta),
                "expected_value_delta_research_only": True,
                "missed_opportunity": bool(attributed and would_block and pnl > 0),
                "preserved_loss": bool(attributed and would_block and pnl < 0),
                "false_positive_observation": bool(attributed and would_allow and pnl < 0),
                "research_only": True,
                "operational_action_allowed": False,
                "can_be_used_as_signal": False,
                "can_be_used_as_veto": False,
            }
        )
    return rows


def compute_attribution_metrics(attribution_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute paper closed trade attribution metrics."""

    rows = [dict(row) for row in attribution_rows]
    attributed_rows = [row for row in rows if row.get("attributed") is True]
    allow_rows = [row for row in attributed_rows if row.get("would_allow") is True]
    block_rows = [row for row in attributed_rows if row.get("would_block") is True]
    ev_values = [
        value
        for value in (_safe_optional_float(row.get("expected_value_delta")) for row in attributed_rows)
        if value is not None
    ]
    survivor_summary: list[dict[str, Any]] = []
    survivor_ids = sorted(
        {
            str(row.get("matched_survivor_rule_id"))
            for row in attributed_rows
            if row.get("matched_survivor_rule_id") not in (None, "")
        }
    )
    for survivor_id in survivor_ids:
        matched = [row for row in attributed_rows if row.get("matched_survivor_rule_id") == survivor_id]
        matched_pnl = [_safe_float(row.get("pnl")) for row in matched]
        matched_ev = [
            value
            for value in (_safe_optional_float(row.get("expected_value_delta")) for row in matched)
            if value is not None
        ]
        survivor_summary.append(
            {
                "survivor_rule_id": survivor_id,
                "attributed_trade_count": len(matched),
                "would_allow_count": sum(1 for row in matched if row.get("would_allow") is True),
                "would_block_count": sum(1 for row in matched if row.get("would_block") is True),
                "net_pnl": _round(sum(matched_pnl)),
                "profit_factor": _profit_factor(matched_pnl),
                "win_rate": _win_rate(matched_pnl),
                "expected_value_delta_total": _round(sum(matched_ev)),
                "expected_value_delta_mean": _round(sum(matched_ev) / len(matched_ev)) if matched_ev else None,
                "missed_opportunity_count": sum(1 for row in matched if row.get("missed_opportunity") is True),
                "preserved_loss_count": sum(1 for row in matched if row.get("preserved_loss") is True),
                "false_positive_observation_count": sum(1 for row in matched if row.get("false_positive_observation") is True),
                "research_only": True,
                "operational_action_allowed": False,
            }
        )
    return {
        "closed_trade_count": len(rows),
        "attributed_trade_count": len(attributed_rows),
        "unattributed_trade_count": len(rows) - len(attributed_rows),
        "would_allow_count": len(allow_rows),
        "would_block_count": len(block_rows),
        "missed_opportunity_count": sum(1 for row in attributed_rows if row.get("missed_opportunity") is True),
        "preserved_loss_count": sum(1 for row in attributed_rows if row.get("preserved_loss") is True),
        "false_positive_observation_count": sum(1 for row in attributed_rows if row.get("false_positive_observation") is True),
        "expected_value_delta_total": _round(sum(ev_values)),
        "expected_value_delta_mean": _round(sum(ev_values) / len(ev_values)) if ev_values else None,
        "attribution_table_sample": rows[:20],
        "survivor_attribution_summary": survivor_summary,
    }


def build_paper_closed_trades_shadow_rule_attribution_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    closed_trades_path: str | Path | None = None,
    closed_trades_source_contract: str | Path | None = None,
    shadow_replay_report: str | Path | None = None,
    observation_design_report: str | Path | None = None,
    oos_report: str | Path | None = None,
    closed_trades: Sequence[Mapping[str, Any]] | None = None,
    replay_rows: Sequence[Mapping[str, Any]] | None = None,
    survivor_records: Sequence[Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only attribution report."""

    root = Path(project_root).resolve()
    inputs = load_attribution_inputs(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        closed_trades_path=closed_trades_path,
        closed_trades_source_contract=closed_trades_source_contract,
        shadow_replay_report=shadow_replay_report,
        observation_design_report=observation_design_report,
        oos_report=oos_report,
        closed_trades=closed_trades,
        replay_rows=replay_rows,
        survivor_records=survivor_records,
    )
    write_requested = bool(write and not no_write)
    can_attribute = inputs.source_status == "ok" and bool(inputs.closed_trades) and (
        bool(inputs.replay_rows) or bool(inputs.survivors)
    )
    join_key = inputs.recommended_join_key or "trade_id"
    attribution_rows = (
        attribute_closed_trades_to_shadow_replay(inputs.closed_trades, inputs.replay_rows, inputs.survivors, join_key=join_key)
        if can_attribute
        else []
    )
    metrics = compute_attribution_metrics(attribution_rows)
    closed_trade_count = len(inputs.closed_trades)
    unattributed_trade_count = (
        metrics["unattributed_trade_count"] if attribution_rows else closed_trade_count
    )
    reason = _reason(inputs, can_attribute=can_attribute)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "input_mode": inputs.input_mode,
        "source_status": inputs.source_status,
        "source_reason": inputs.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "closed_trades_source_path": inputs.closed_trades_source_path,
        "closed_trades_source_sha256": inputs.closed_trades_source_sha256,
        "closed_trades_source_contract_path": inputs.closed_trades_source_contract_path,
        "closed_trades_source_contract_sha256": inputs.closed_trades_source_contract_sha256,
        "recommended_join_key": inputs.recommended_join_key,
        "join_key_used": join_key,
        "shadow_replay_source_path": inputs.shadow_replay_source_path,
        "shadow_replay_source_sha256": inputs.shadow_replay_source_sha256,
        "replay_row_count": len(inputs.replay_rows),
        "survivor_record_count": len(inputs.survivors),
        "closed_trade_count": closed_trade_count,
        "attributed_trade_count": metrics["attributed_trade_count"],
        "unattributed_trade_count": unattributed_trade_count,
        "would_allow_count": metrics["would_allow_count"],
        "would_block_count": metrics["would_block_count"],
        "missed_opportunity_count": metrics["missed_opportunity_count"],
        "preserved_loss_count": metrics["preserved_loss_count"],
        "false_positive_observation_count": metrics["false_positive_observation_count"],
        "expected_value_delta_total": metrics["expected_value_delta_total"],
        "expected_value_delta_mean": metrics["expected_value_delta_mean"],
        "attribution_table_sample": metrics["attribution_table_sample"],
        "survivor_attribution_summary": metrics["survivor_attribution_summary"],
        "gate_summary": _gate_summary(can_attribute),
        "safety_flags": dict(SAFETY_FLAGS),
        "attribution_semantics": {
            "would_allow": "closed paper trade belonged to the research-only survivor/replay cohort",
            "would_block": "closed paper trade stayed outside the research-only survivor/replay cohort",
            "missed_opportunity": "would_block trade with positive closed PnL",
            "preserved_loss": "would_block trade with negative closed PnL",
            "false_positive_observation": "would_allow trade with negative closed PnL",
            "operational_use": "forbidden: descriptive evidence only, not a signal, permission, veto or runtime rule",
        },
        "required_fields": list(REQUIRED_TOP_LEVEL_FIELDS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_attribution_report(report)

    if write_requested:
        output_path = _resolve_output_report(root, output_report)
        output_error = _validate_output_report_path(root, output_path)
        if output_error is not None:
            report["reason"] = output_error
            report["validation_errors"] = validate_attribution_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
    return report


def _reason(inputs: LoadedAttributionInputs, *, can_attribute: bool) -> str:
    if inputs.source_status != "ok":
        if inputs.input_mode == "no_runtime_rows_loaded":
            return "paper_shadow_attribution_requires_explicit_runtime_read_or_in_memory_inputs"
        return inputs.source_reason
    if not inputs.closed_trades:
        return "paper_shadow_attribution_blocked_no_closed_trades"
    if not inputs.replay_rows and not inputs.survivors:
        return "paper_shadow_attribution_blocked_no_shadow_replay_or_survivors"
    if not can_attribute:
        return "paper_shadow_attribution_blocked_no_attributable_rows"
    return "paper_shadow_attribution_completed_research_only_no_operational_authority"


def _gate_summary(can_attribute: bool) -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
        "attribution_computed": can_attribute,
        "operational_authority": False,
        "ready_for_shadow_observation": False,
        "can_promote_rules": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
        "result_can_be_used_for_operations": False,
    }


def validate_attribution_report(report: Mapping[str, Any]) -> list[str]:
    """Validate the non-operational attribution contract."""

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
        safety_flags = report.get("safety_flags")
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


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
