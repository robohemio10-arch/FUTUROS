"""Read-only source contract for closed paper trades.

This module discovers and normalizes already exported closed paper trades for
research-only replay/attribution. It does not read private exchange APIs, mutate
runtime state, write SQLite/Parquet, change risk, promote rules, or emit orders.
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


SCHEMA_VERSION = "paper_closed_trades_readonly_source_contract_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION_RESEARCH = "MANTER_EM_RESEARCH"
DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_closed_trades_readonly_source_contract_v1.md")

DEFAULT_CANDIDATE_SOURCES = (
    "data/trades/inbox/freqtrade_paper_closed_trades.csv",
    "data/reports/freqtrade_paper_closed_trades.csv",
    "data/reports/freqtrade_paper_closed_trades.json",
    "data/reports/paper_closed_trades.json",
    "data/reports/phase14_closed_trades.json",
    "data/reports/phase14_paper_closed_trades.json",
    "data/feedback/paper_closed_trades_incremental.parquet",
    "data/runtime/freqtrade_paper_closed_trades.csv",
    "data/runtime/phase14/freqtrade_paper_closed_trades.csv",
)

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "trade_id": ("trade_id", "ft_trade_id", "freqtrade_trade_id", "id", "tradeId"),
    "order_id": ("order_id", "orderId", "orderid", "exchange_order_id"),
    "internal_order_id": ("internal_order_id", "internal_id", "client_order_id", "clientOrderId"),
    "symbol": ("symbol", "pair", "moeda", "asset", "market"),
    "side": ("side", "trade_side", "direction", "fechar_side", "position_side"),
    "open_time": ("open_time", "open_time_utc", "opened_at", "date_open", "horario_abertura", "open_date"),
    "close_time": ("close_time", "close_time_utc", "closed_at", "date_close", "horario_fechamento", "close_date"),
    "entry_price": ("entry_price", "open_rate", "open_price", "preco_abertura", "price_open"),
    "exit_price": ("exit_price", "close_rate", "close_price", "preco_fechamento", "price_close"),
    "amount": ("amount", "trade_amount", "qty", "quantity", "contracts"),
    "stake_amount": ("stake_amount", "stake", "notional", "cost"),
    "pnl": (
        "pnl",
        "profit_abs",
        "net_pnl",
        "pnl_fechado",
        "reported_pnl_usdt",
        "realized_pnl",
        "close_profit_abs",
        "raw_pnl_usdt",
    ),
    "profit_ratio": (
        "profit_ratio",
        "close_profit",
        "return_pct",
        "normalized_return_pct",
        "taxa_lucros_perdas_fechados_pct",
    ),
    "fee": ("fee", "fees", "fee_open", "fee_close", "total_fee"),
}

REQUIRED_CANONICAL_FIELDS = (
    "trade_id",
    "symbol",
    "side",
    "open_time",
    "close_time",
    "entry_price",
    "exit_price",
    "pnl",
)

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
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "exchange_private_access": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "registers_shadow_rules": False,
    "applies_shadow_rules": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "writes_data_by_default": False,
}


@dataclass(frozen=True)
class SourceCandidate:
    path: Path
    rows: list[dict[str, Any]]
    status: str
    reason: str
    source_type: str
    sha256: str | None
    schema_columns: list[str]


@dataclass(frozen=True)
class LoadedClosedTradeSources:
    candidates: list[SourceCandidate]
    input_mode: str
    source_status: str
    source_reason: str
    candidate_sources_checked: list[str]
    candidate_sources_missing: list[str]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (project_root / path).resolve()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "none", "nat", "null"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _safe_float(value: object) -> float | None:
    text = _clean_text(value)
    if text is None:
        return None
    normalized = text.replace("%", "").replace(",", ".")
    try:
        numeric = float(normalized)
    except ValueError:
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _normalize_symbol(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    return text.upper().replace("/", "").replace("-", "").replace("_", "")


def _normalize_side(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"buy", "long", "comprado", "entry_long"}:
        return "long"
    if lowered in {"sell", "short", "vendido", "entry_short"}:
        return "short"
    if "short" in lowered:
        return "short"
    if "long" in lowered:
        return "long"
    return lowered


def _normalize_time(value: object) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    normalized = text.replace(" ", "T")
    if normalized.endswith("Z") or "+" in normalized[-6:]:
        return normalized
    return f"{normalized}Z"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _row_fingerprint(row: Mapping[str, Any]) -> str:
    fingerprint_fields = {
        "trade_id": row.get("trade_id"),
        "order_id": row.get("order_id"),
        "internal_order_id": row.get("internal_order_id"),
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "open_time": row.get("open_time"),
        "close_time": row.get("close_time"),
        "entry_price": row.get("entry_price"),
        "exit_price": row.get("exit_price"),
        "pnl": row.get("pnl"),
    }
    return _sha256_text(_canonical_json(fingerprint_fields))


def _field_mapping(columns: Sequence[str]) -> dict[str, str | None]:
    normalized_to_original = {column.strip().lower(): column for column in columns}
    mapping: dict[str, str | None] = {}
    for canonical, candidates in FIELD_CANDIDATES.items():
        match = None
        for candidate in candidates:
            original = normalized_to_original.get(candidate.lower())
            if original is not None:
                match = original
                break
        mapping[canonical] = match
    return mapping


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _extract_json_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("closed_trades", "trades", "rows", "records", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, Mapping)]
            if isinstance(value, Mapping):
                nested = _extract_json_rows(value)
                if nested:
                    return nested
    return []


def _read_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return _extract_json_rows(payload)


def _read_with_pandas(path: Path) -> list[dict[str, Any]]:
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, dtype=object)
    else:
        raise ValueError(f"unsupported_pandas_source:{suffix or 'no_suffix'}")
    return [dict(row) for row in frame.to_dict(orient="records")]


def _read_source(path: Path) -> tuple[list[dict[str, Any]], str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path), "csv"
    if suffix == ".json":
        return _read_json(path), "json"
    if suffix in {".parquet", ".xlsx", ".xls"}:
        return _read_with_pandas(path), suffix.removeprefix(".")
    return [], f"unsupported_source:{suffix or 'no_suffix'}"


def _columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for column in row:
            seen[str(column)] = None
    return sorted(seen)


def load_closed_trade_source_candidates(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    source_paths: Sequence[str | Path] | None = None,
    source_rows: Sequence[Mapping[str, Any]] | None = None,
) -> LoadedClosedTradeSources:
    """Load closed trade candidate sources without mutating runtime state."""

    root = Path(project_root).resolve()
    if source_rows is not None:
        rows = [dict(row) for row in source_rows]
        candidate = SourceCandidate(
            path=root / "<in_memory_closed_trades>",
            rows=rows,
            status="ok" if rows else "blocked",
            reason="in_memory_rows_supplied" if rows else "empty_in_memory_rows",
            source_type="in_memory",
            sha256=None,
            schema_columns=_columns(rows),
        )
        return LoadedClosedTradeSources(
            candidates=[candidate] if rows else [],
            input_mode="in_memory_closed_trade_rows",
            source_status="ok" if rows else "blocked",
            source_reason="in_memory_rows_supplied" if rows else "empty_in_memory_rows",
            candidate_sources_checked=["<in_memory_closed_trades>"],
            candidate_sources_missing=[],
        )

    checked_values = [str(item) for item in source_paths] if source_paths else list(DEFAULT_CANDIDATE_SOURCES)
    if not allow_runtime_read:
        return LoadedClosedTradeSources(
            candidates=[],
            input_mode="no_runtime_rows_loaded",
            source_status="blocked",
            source_reason="runtime_read_not_allowed_by_default",
            candidate_sources_checked=checked_values,
            candidate_sources_missing=checked_values,
        )

    candidates: list[SourceCandidate] = []
    missing: list[str] = []
    for raw_path in checked_values:
        path = _resolve_path(root, raw_path)
        relative = _project_relative(path, root)
        if not path.exists() or not path.is_file():
            missing.append(relative)
            continue
        try:
            rows, source_type = _read_source(path)
        except (OSError, ValueError, json.JSONDecodeError, csv.Error, ImportError) as exc:
            candidates.append(
                SourceCandidate(
                    path=path,
                    rows=[],
                    status="blocked",
                    reason=f"source_read_failed:{type(exc).__name__}",
                    source_type="unreadable",
                    sha256=_sha256_file(path),
                    schema_columns=[],
                )
            )
            continue
        candidates.append(
            SourceCandidate(
                path=path,
                rows=rows,
                status="ok" if rows else "blocked",
                reason="source_loaded_read_only" if rows else "source_has_no_rows",
                source_type=source_type,
                sha256=_sha256_file(path),
                schema_columns=_columns(rows),
            )
        )

    present = [candidate for candidate in candidates if candidate.status == "ok" and candidate.rows]
    return LoadedClosedTradeSources(
        candidates=candidates,
        input_mode="runtime_read_requested",
        source_status="ok" if present else "blocked",
        source_reason="sources_loaded_read_only" if present else "no_supported_closed_trade_sources",
        candidate_sources_checked=checked_values,
        candidate_sources_missing=missing,
    )


def normalize_closed_trade_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_path: str | None = None,
    source_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str | None]]:
    """Normalize source rows to the closed trade source contract."""

    columns = _columns(rows)
    mapping = _field_mapping(columns)
    normalized: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        trade_id_source = mapping.get("trade_id")
        order_id_source = mapping.get("order_id")
        internal_order_id_source = mapping.get("internal_order_id")
        raw_trade_id = row.get(trade_id_source) if trade_id_source else None
        raw_order_id = row.get(order_id_source) if order_id_source else None
        raw_internal_order_id = row.get(internal_order_id_source) if internal_order_id_source else None
        canonical: dict[str, Any] = {
            "trade_id": _clean_text(raw_trade_id),
            "order_id": _clean_text(raw_order_id),
            "internal_order_id": _clean_text(raw_internal_order_id),
            "symbol": _normalize_symbol(_mapped_value(row, mapping, "symbol")),
            "side": _normalize_side(_mapped_value(row, mapping, "side")),
            "open_time": _normalize_time(_mapped_value(row, mapping, "open_time")),
            "close_time": _normalize_time(_mapped_value(row, mapping, "close_time")),
            "entry_price": _safe_float(_mapped_value(row, mapping, "entry_price")),
            "exit_price": _safe_float(_mapped_value(row, mapping, "exit_price")),
            "amount": _safe_float(_mapped_value(row, mapping, "amount")),
            "stake_amount": _safe_float(_mapped_value(row, mapping, "stake_amount")),
            "pnl": _safe_float(_mapped_value(row, mapping, "pnl")),
            "profit_ratio": _safe_float(_mapped_value(row, mapping, "profit_ratio")),
            "fee": _safe_float(_mapped_value(row, mapping, "fee")),
            "source_path": source_path,
            "source_sha256": source_sha256,
            "source_row_index": index,
        }
        if canonical["trade_id"] is None:
            fallback_key = {
                "order_id": canonical["order_id"],
                "internal_order_id": canonical["internal_order_id"],
                "symbol": canonical["symbol"],
                "side": canonical["side"],
                "open_time": canonical["open_time"],
                "close_time": canonical["close_time"],
                "entry_price": canonical["entry_price"],
                "exit_price": canonical["exit_price"],
                "pnl": canonical["pnl"],
            }
            canonical["trade_id"] = f"paper_closed_{_sha256_text(_canonical_json(fallback_key))[:16]}"
            canonical["trade_id_generated"] = True
        else:
            canonical["trade_id_generated"] = False
        canonical["row_fingerprint"] = _row_fingerprint(canonical)
        missing = _missing_required_for_row(canonical)
        if missing:
            rejected.append(
                {
                    "source_row_index": index,
                    "missing_required_fields": missing,
                    "row_fingerprint": canonical["row_fingerprint"],
                }
            )
            continue
        normalized.append(canonical)
    return normalized, rejected, mapping


def _mapped_value(row: Mapping[str, Any], mapping: Mapping[str, str | None], canonical_field: str) -> Any:
    source_field = mapping.get(canonical_field)
    if source_field is None:
        return None
    return row.get(source_field)


def _missing_required_for_row(row: Mapping[str, Any]) -> list[str]:
    return [field for field in REQUIRED_CANONICAL_FIELDS if row.get(field) in (None, "")]


def _source_schema_summary(candidates: Sequence[SourceCandidate], root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": _project_relative(candidate.path, root),
            "status": candidate.status,
            "reason": candidate.reason,
            "source_type": candidate.source_type,
            "rows": len(candidate.rows),
            "columns": list(candidate.schema_columns),
            "sha256": candidate.sha256,
        }
        for candidate in candidates
    ]


def _candidate_sources_present(candidates: Sequence[SourceCandidate], root: Path) -> list[str]:
    return [
        _project_relative(candidate.path, root)
        for candidate in candidates
        if candidate.status == "ok" and candidate.rows
    ]


def _join_key_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for field in ("order_id", "internal_order_id", "trade_id", "row_fingerprint"):
        values = [str(row.get(field)) for row in rows if row.get(field) not in (None, "")]
        unique_count = len(set(values))
        duplicate_count = len(values) - unique_count
        candidates.append(
            {
                "field": field,
                "available_rows": len(values),
                "coverage": round(len(values) / len(rows), 10) if rows else 0.0,
                "unique_count": unique_count,
                "duplicate_count": duplicate_count,
                "is_unique": bool(rows and len(values) == len(rows) and duplicate_count == 0),
            }
        )
    return candidates


def _recommended_join_key(join_candidates: Sequence[Mapping[str, Any]]) -> str | None:
    for preferred in ("order_id", "internal_order_id", "trade_id", "row_fingerprint"):
        for candidate in join_candidates:
            if candidate.get("field") == preferred and candidate.get("is_unique") is True:
                return preferred
    return None


def _missing_required_fields(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    if not rows:
        return list(REQUIRED_CANONICAL_FIELDS)
    missing: list[str] = []
    for field in REQUIRED_CANONICAL_FIELDS:
        if not all(row.get(field) not in (None, "") for row in rows):
            missing.append(field)
    return missing


def compute_source_contract(
    candidates: Sequence[SourceCandidate],
    *,
    project_root: str | Path,
) -> dict[str, Any]:
    """Compute canonical source contract diagnostics from loaded candidates."""

    root = Path(project_root).resolve()
    selected = next((candidate for candidate in candidates if candidate.status == "ok" and candidate.rows), None)
    normalized: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    mapping = {field: None for field in FIELD_CANDIDATES}
    if selected is not None:
        normalized, rejected, mapping = normalize_closed_trade_rows(
            selected.rows,
            source_path=_project_relative(selected.path, root),
            source_sha256=selected.sha256,
        )
    missing_required = _missing_required_fields(normalized)
    join_candidates = _join_key_candidates(normalized)
    recommended_join_key = _recommended_join_key(join_candidates)
    duplicate_key_count = _duplicate_count_for_key(normalized, recommended_join_key)
    contract_complete = bool(normalized) and not missing_required and recommended_join_key is not None
    root_causes = _root_cause_candidates(
        selected_source_present=selected is not None,
        normalized_count=len(normalized),
        rejected_count=len(rejected),
        missing_required=missing_required,
        recommended_join_key=recommended_join_key,
        duplicate_key_count=duplicate_key_count,
    )
    return {
        "source_contract_status": "ok" if contract_complete else "blocked",
        "selected_source_path": _project_relative(selected.path, root) if selected is not None else None,
        "source_schema_summary": _source_schema_summary(candidates, root),
        "canonical_field_mapping": mapping,
        "missing_required_fields": missing_required,
        "normalized_closed_trade_count": len(normalized),
        "rejected_row_count": len(rejected),
        "rejected_rows_sample": rejected[:20],
        "duplicate_key_count": duplicate_key_count,
        "join_key_candidates": join_candidates,
        "recommended_join_key": recommended_join_key,
        "replay_ready": contract_complete,
        "attribution_ready": contract_complete,
        "root_cause_candidates": root_causes,
        "recommended_next_action": _recommended_next_action(root_causes),
        "normalized_rows_sample": normalized[:20],
    }


def _duplicate_count_for_key(rows: Sequence[Mapping[str, Any]], key: str | None) -> int:
    if not key:
        return 0
    values = [str(row.get(key)) for row in rows if row.get(key) not in (None, "")]
    return len(values) - len(set(values))


def _root_cause_candidates(
    *,
    selected_source_present: bool,
    normalized_count: int,
    rejected_count: int,
    missing_required: Sequence[str],
    recommended_join_key: str | None,
    duplicate_key_count: int,
) -> list[str]:
    causes: list[str] = []
    if not selected_source_present:
        causes.append("no_readable_closed_trades_source")
    if selected_source_present and normalized_count == 0:
        causes.append("closed_trades_source_has_no_contract_valid_rows")
    if rejected_count > 0:
        causes.append("closed_trade_rows_rejected_by_required_field_contract")
    if missing_required:
        causes.append("closed_trades_missing_required_canonical_fields")
    if recommended_join_key is None:
        causes.append("no_unique_join_key_available")
    if duplicate_key_count > 0:
        causes.append("recommended_join_key_has_duplicates")
    return sorted(set(causes))


def _recommended_next_action(root_causes: Sequence[str]) -> str:
    if not root_causes:
        return "usar_fonte_readonly_normalizada_como_input_de_replay_attribution_sem_liberar_observacao"
    if "no_readable_closed_trades_source" in root_causes:
        return "materializar_export_readonly_de_trades_fechados_paper_e_reexecutar_contrato"
    if "no_unique_join_key_available" in root_causes:
        return "adicionar_identificador_estavel_readonly_ou_usar_row_fingerprint_como_join_key_de_research"
    return "corrigir_schema_da_fonte_readonly_de_closed_trades_sem_alterar_runtime"


def build_paper_closed_trades_readonly_source_contract_report(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    source_paths: Sequence[str | Path] | None = None,
    source_rows: Sequence[Mapping[str, Any]] | None = None,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the research-only closed trades source contract report."""

    root = Path(project_root).resolve()
    loaded = load_closed_trade_source_candidates(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        source_paths=source_paths,
        source_rows=source_rows,
    )
    contract = compute_source_contract(loaded.candidates, project_root=root)
    write_requested = bool(write and not no_write)
    reason = _reason(loaded, contract)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "status": "blocked",
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "input_mode": loaded.input_mode,
        "source_status": loaded.source_status,
        "source_reason": loaded.source_reason,
        "allow_runtime_read": allow_runtime_read,
        "candidate_sources_checked": loaded.candidate_sources_checked,
        "candidate_sources_present": _candidate_sources_present(loaded.candidates, root),
        "candidate_sources_missing": loaded.candidate_sources_missing,
        "source_contract_status": contract["source_contract_status"],
        "source_schema_summary": contract["source_schema_summary"],
        "selected_source_path": contract["selected_source_path"],
        "normalized_closed_trade_count": contract["normalized_closed_trade_count"],
        "required_fields": list(REQUIRED_CANONICAL_FIELDS),
        "missing_required_fields": contract["missing_required_fields"],
        "canonical_field_mapping": contract["canonical_field_mapping"],
        "rejected_row_count": contract["rejected_row_count"],
        "rejected_rows_sample": contract["rejected_rows_sample"],
        "duplicate_key_count": contract["duplicate_key_count"],
        "join_key_candidates": contract["join_key_candidates"],
        "recommended_join_key": contract["recommended_join_key"],
        "replay_ready": contract["replay_ready"],
        "attribution_ready": contract["attribution_ready"],
        "root_cause_candidates": contract["root_cause_candidates"],
        "recommended_next_action": contract["recommended_next_action"],
        "normalized_rows_sample": contract["normalized_rows_sample"],
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "gate_summary": _gate_summary(contract),
        "safety_flags": dict(SAFETY_FLAGS),
        "validation_errors": [],
        **SAFETY_FLAGS,
    }
    report["validation_errors"] = validate_source_contract_report(report)

    if write_requested:
        output_path = _resolve_output_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_output_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["reason"] = output_error or markdown_error
            report["validation_errors"] = validate_source_contract_report(report)
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def _reason(loaded: LoadedClosedTradeSources, contract: Mapping[str, Any]) -> str:
    if loaded.source_status != "ok":
        if loaded.input_mode == "no_runtime_rows_loaded":
            return "closed_trades_source_contract_requires_explicit_runtime_read_or_in_memory_inputs"
        return loaded.source_reason
    if contract.get("source_contract_status") != "ok":
        return "closed_trades_source_contract_incomplete"
    return "closed_trades_source_contract_complete_research_only_no_operational_authority"


def _gate_summary(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision": DECISION_RESEARCH,
        "source_contract_status": contract.get("source_contract_status"),
        "replay_ready": bool(contract.get("replay_ready")),
        "attribution_ready": bool(contract.get("attribution_ready")),
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        "operational_authority": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_runtime": False,
        "result_can_be_used_for_operations": False,
    }


def validate_source_contract_report(report: Mapping[str, Any]) -> list[str]:
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
    required = (
        "status",
        "reason",
        "decision",
        "schema_version",
        "input_mode",
        "candidate_sources_checked",
        "candidate_sources_present",
        "candidate_sources_missing",
        "source_contract_status",
        "normalized_closed_trade_count",
        "required_fields",
        "missing_required_fields",
        "canonical_field_mapping",
        "join_key_candidates",
        "recommended_join_key",
        "replay_ready",
        "attribution_ready",
        "gate_summary",
        "safety_flags",
        "write_performed",
    )
    for field in required:
        if field not in report:
            errors.append(f"missing_required_field:{field}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    root_causes = report.get("root_cause_candidates", [])
    root_cause_lines = "\n".join(f"- `{cause}`" for cause in root_causes) if root_causes else "- none"
    return "\n".join(
        [
            "# Paper Closed Trades Read-Only Source Contract V1",
            "",
            f"- Decision: `{report.get('decision')}`",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Source contract status: `{report.get('source_contract_status')}`",
            f"- Normalized closed trades: `{report.get('normalized_closed_trade_count')}`",
            f"- Recommended join key: `{report.get('recommended_join_key')}`",
            f"- Replay ready: `{report.get('replay_ready')}`",
            f"- Attribution ready: `{report.get('attribution_ready')}`",
            "",
            "## Root Cause Candidates",
            "",
            root_cause_lines,
            "",
            "## Operational Boundary",
            "",
            "This report is research-only and read-only. It does not authorize paper observation, survivor promotion, runtime integration, risk changes, orders or private exchange access.",
            "",
        ]
    )


def _resolve_output_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def _validate_output_path(root: Path, path: Path, *, suffix: str) -> str | None:
    reports_dir = (root / "data" / "reports").resolve()
    try:
        path.relative_to(reports_dir)
    except ValueError:
        return "write_blocked_output_must_be_under_data_reports"
    if path.suffix.lower() != suffix:
        return f"write_blocked_output_must_be_{suffix.removeprefix('.')}_report"
    return None
