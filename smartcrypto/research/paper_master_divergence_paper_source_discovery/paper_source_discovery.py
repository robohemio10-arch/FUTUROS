"""Read-only paper source discovery for Paper/Master divergence research.

The module discovers candidate Paper/Freqtrade trade-export files without
copying, mutating, or versioning runtime/data artifacts. Runtime filesystem
inspection is explicitly opt-in via ``allow_runtime_read``.

Safety contract:
- no Freqtrade/RiskManager/Qlib/AI Shadow runtime updates;
- no order submission;
- no exchange private access;
- no promotion of rules or models;
- no writes by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "paper_master_divergence_paper_source_discovery_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "MANTER_EM_RESEARCH"
HYPOTHESIS_SCOPE = ["H1", "H2", "H6"]
OOS_SLICE_DIMENSIONS = [
    "day",
    "symbol",
    "side",
    "exit_reason",
    "duration_bucket",
    "covered_vs_uncovered",
]
SUPPORTED_SUFFIXES = {".csv", ".json", ".jsonl", ".xlsx", ".xls", ".sqlite", ".sqlite3", ".db"}
METADATA_ONLY_SUFFIXES = {".xlsx", ".xls"}
DEFAULT_DISCOVERY_ROOTS = [
    "data/reports",
    "data/runtime",
    "data/freqtrade",
    "user_data",
    "freqtrade",
    "logs",
]
POSITIVE_NAME_KEYWORDS = [
    "paper",
    "freqtrade",
    "trade",
    "trades",
    "closed",
    "history",
    "profit",
    "runtime",
    "dry",
]
NEGATIVE_NAME_KEYWORDS = [
    "master",
    "ocr",
    "research_dataset",
    "candidate",
    "registry",
    "model",
    "qlib",
    "backup",
]
PAPER_SCHEMA_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "pair", "trade_pair", "market", "instrument"),
    "side": ("side", "direction", "is_short", "position_side"),
    "pnl": ("pnl", "profit_abs", "profit", "realized_profit", "close_profit_abs"),
    "close_time": ("close_time", "close_date", "close_timestamp", "exit_time", "sell_date"),
    "exit_reason": ("exit_reason", "sell_reason", "close_reason", "reason"),
    "duration": ("duration", "duration_minutes", "trade_duration", "open_duration"),
}

CANONICAL_DIVERGENCE_METRICS: dict[str, Any] = {
    "paper_minus_master_net_pnl": -164.52110752,
    "paper_minus_master_profit_factor": -1.269242,
    "paper_minus_master_trade_count": -4,
    "paper_minus_master_win_rate_points": -30.1961,
    "paper_replicates_master_edge": False,
}

FORBIDDEN_ACTIONS = [
    "copiar ou versionar runtime/data",
    "aplicar regra no Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "registrar ou promover candidate rule",
    "promover modelo",
    "executar treino operacional",
    "habilitar live ou canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade por padrão",
]


@dataclass(frozen=True)
class PaperSourceCandidate:
    """Metadata for a candidate paper trade source."""

    path: str
    exists: bool
    suffix: str | None
    source_type: str | None
    size_bytes: int | None
    mtime_utc: str | None
    sha256: str | None
    discovery_status: str
    reason: str
    score: int
    confidence: str
    row_count_estimate: int | None
    schema_status: str
    schema_columns_detected: list[str]
    matched_schema_fields: list[str]
    positive_signals: list[str]
    negative_signals: list[str]
    requires_manual_review: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "suffix": self.suffix,
            "source_type": self.source_type,
            "size_bytes": self.size_bytes,
            "mtime_utc": self.mtime_utc,
            "sha256": self.sha256,
            "discovery_status": self.discovery_status,
            "reason": self.reason,
            "score": self.score,
            "confidence": self.confidence,
            "row_count_estimate": self.row_count_estimate,
            "schema_status": self.schema_status,
            "schema_columns_detected": self.schema_columns_detected,
            "matched_schema_fields": self.matched_schema_fields,
            "positive_signals": self.positive_signals,
            "negative_signals": self.negative_signals,
            "requires_manual_review": self.requires_manual_review,
        }


def _safety_flags() -> dict[str, Any]:
    return {
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "release_authority": False,
        "readiness_release_authority": False,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "executes_scheduler": False,
        "executes_orchestrator": False,
        "executes_stage_builders": False,
        "runs_training": False,
        "applies_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
        "registers_candidate_rules": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "remediation_application_allowed": False,
        "ready_for_candidate_registry": False,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _resolve_path(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path


def _relative_path(project_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def _collect_name_signals(path: Path) -> tuple[list[str], list[str]]:
    lowered = str(path).replace("\\", "/").lower()
    positive = [keyword for keyword in POSITIVE_NAME_KEYWORDS if keyword in lowered]
    negative = [keyword for keyword in NEGATIVE_NAME_KEYWORDS if keyword in lowered]
    return positive, negative


def _confidence_from_score(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 55:
        return "medium"
    if score >= 25:
        return "low"
    return "rejected"


def _read_csv_header_and_count(path: Path) -> tuple[list[str], int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        fieldnames = [str(name).strip() for name in (reader.fieldnames or []) if str(name).strip()]
        count = sum(1 for _ in reader)
    return fieldnames, count


def _json_rows_payload(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("rows", "trades", "data", "records", "closed_trades"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
        return [payload]
    return []


def _read_json_columns_and_count(path: Path) -> tuple[list[str], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _json_rows_payload(payload)
    columns: set[str] = set()
    for row in rows[:25]:
        columns.update(str(key).strip() for key in row.keys() if str(key).strip())
    return sorted(columns), len(rows)


def _read_jsonl_columns_and_count(path: Path) -> tuple[list[str], int]:
    columns: set[str] = set()
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if isinstance(payload, Mapping):
                count += 1
                if count <= 25:
                    columns.update(str(key).strip() for key in payload.keys() if str(key).strip())
    return sorted(columns), count


def _read_sqlite_columns_and_count(path: Path) -> tuple[list[str], int, str | None]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return [], 0, None
    try:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [str(row[0]) for row in table_rows]
        preferred = [
            name for name in table_names if any(token in name.lower() for token in ("trade", "order"))
        ]
        selected_table = preferred[0] if preferred else (table_names[0] if table_names else None)
        if selected_table is None:
            return [], 0, None
        columns = [
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({selected_table!r})").fetchall()
        ]
        count_row = connection.execute(f"SELECT COUNT(*) FROM {selected_table!r}").fetchone()
        count = int(count_row[0]) if count_row else 0
        return columns, count, selected_table
    finally:
        connection.close()


def _match_schema_fields(columns: Sequence[str]) -> list[str]:
    lowered = {column.strip().lower() for column in columns}
    matched: list[str] = []
    for field, aliases in PAPER_SCHEMA_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            matched.append(field)
    return matched


def _schema_status(columns: Sequence[str]) -> tuple[str, list[str]]:
    matched = _match_schema_fields(columns)
    required = {"symbol", "pnl", "close_time"}
    matched_set = set(matched)
    if required.issubset(matched_set):
        return "candidate_trade_schema", matched
    if len(matched) >= 2:
        return "partial_trade_schema", matched
    if columns:
        return "unknown_schema", matched
    return "metadata_only_or_unreadable_schema", matched


def inspect_candidate_path(project_root: Path, candidate_path: Path) -> PaperSourceCandidate:
    path = candidate_path
    exists = path.exists()
    suffix = path.suffix.lower() if (suffix := path.suffix) else None
    source_type = suffix[1:] if suffix else None
    positive_signals, negative_signals = _collect_name_signals(path)

    if not exists:
        return PaperSourceCandidate(
            path=_relative_path(project_root, path),
            exists=False,
            suffix=suffix,
            source_type=source_type,
            size_bytes=None,
            mtime_utc=None,
            sha256=None,
            discovery_status="missing_source",
            reason="candidate_path_does_not_exist",
            score=0,
            confidence="rejected",
            row_count_estimate=None,
            schema_status="missing_source",
            schema_columns_detected=[],
            matched_schema_fields=[],
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            requires_manual_review=True,
        )

    if path.is_dir():
        return PaperSourceCandidate(
            path=_relative_path(project_root, path),
            exists=True,
            suffix=None,
            source_type="directory",
            size_bytes=None,
            mtime_utc=_mtime_utc(path),
            sha256=None,
            discovery_status="ignored_directory",
            reason="candidate_path_is_directory",
            score=0,
            confidence="rejected",
            row_count_estimate=None,
            schema_status="directory",
            schema_columns_detected=[],
            matched_schema_fields=[],
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            requires_manual_review=True,
        )

    if suffix not in SUPPORTED_SUFFIXES:
        return PaperSourceCandidate(
            path=_relative_path(project_root, path),
            exists=True,
            suffix=suffix,
            source_type=source_type,
            size_bytes=path.stat().st_size,
            mtime_utc=_mtime_utc(path),
            sha256=_sha256_file(path),
            discovery_status="unsupported_source_type",
            reason="unsupported_suffix_for_paper_trade_discovery",
            score=0,
            confidence="rejected",
            row_count_estimate=None,
            schema_status="unsupported_source_type",
            schema_columns_detected=[],
            matched_schema_fields=[],
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            requires_manual_review=True,
        )

    columns: list[str] = []
    row_count: int | None = None
    reason = "metadata_inspected"
    try:
        if suffix == ".csv":
            columns, row_count = _read_csv_header_and_count(path)
        elif suffix == ".json":
            columns, row_count = _read_json_columns_and_count(path)
        elif suffix == ".jsonl":
            columns, row_count = _read_jsonl_columns_and_count(path)
        elif suffix in {".sqlite", ".sqlite3", ".db"}:
            columns, count, table = _read_sqlite_columns_and_count(path)
            row_count = count
            reason = f"sqlite_metadata_inspected:{table or 'no_table'}"
        elif suffix in METADATA_ONLY_SUFFIXES:
            reason = "spreadsheet_metadata_only_manual_review_required"
    except Exception as exc:  # noqa: BLE001 - defensive metadata scanner
        reason = f"metadata_read_error:{type(exc).__name__}"
        columns = []
        row_count = None

    schema_status, matched_fields = _schema_status(columns)
    score = 0
    score += min(len(positive_signals) * 12, 36)
    score -= min(len(negative_signals) * 18, 54)
    if source_type in {"csv", "json", "jsonl"}:
        score += 12
    if source_type in {"sqlite", "sqlite3", "db"}:
        score += 18
    if source_type in {"xlsx", "xls"}:
        score += 6
    if schema_status == "candidate_trade_schema":
        score += 45
    elif schema_status == "partial_trade_schema":
        score += 22
    if row_count and row_count > 0:
        score += 10
    if "paper" in positive_signals:
        score += 16
    if "master" in negative_signals:
        score -= 35
    score = max(0, min(score, 100))
    confidence = _confidence_from_score(score)
    status = "candidate" if confidence != "rejected" else "rejected_low_confidence"

    return PaperSourceCandidate(
        path=_relative_path(project_root, path),
        exists=True,
        suffix=suffix,
        source_type=source_type,
        size_bytes=path.stat().st_size,
        mtime_utc=_mtime_utc(path),
        sha256=_sha256_file(path),
        discovery_status=status,
        reason=reason,
        score=score,
        confidence=confidence,
        row_count_estimate=row_count,
        schema_status=schema_status,
        schema_columns_detected=columns,
        matched_schema_fields=matched_fields,
        positive_signals=positive_signals,
        negative_signals=negative_signals,
        requires_manual_review=True,
    )


def _iter_files(root: Path, *, max_files: int) -> Iterable[Path]:
    count = 0
    if not root.exists() or not root.is_dir():
        return
    for path in root.rglob("*"):
        if count >= max_files:
            break
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        count += 1
        yield path


def discover_paper_source_candidates(
    *,
    project_root: str | Path,
    allow_runtime_read: bool = False,
    candidate_sources: Sequence[str | Path] | None = None,
    discovery_roots: Sequence[str | Path] | None = None,
    max_files_per_root: int = 250,
) -> list[PaperSourceCandidate]:
    """Discover paper-source candidates without mutating project state."""
    root = Path(project_root).resolve()
    if not allow_runtime_read:
        return []

    ordered_paths: list[Path] = []
    seen: set[str] = set()

    def add_path(path_value: str | Path) -> None:
        path = _resolve_path(root, path_value)
        marker = str(path.resolve()) if path.exists() else str(path)
        if marker not in seen:
            seen.add(marker)
            ordered_paths.append(path)

    for candidate in candidate_sources or []:
        add_path(candidate)

    roots = discovery_roots if discovery_roots is not None else DEFAULT_DISCOVERY_ROOTS
    for discovery_root in roots:
        base = _resolve_path(root, discovery_root)
        if base.is_file():
            add_path(base)
            continue
        for file_path in _iter_files(base, max_files=max_files_per_root):
            add_path(file_path)

    candidates = [inspect_candidate_path(root, path) for path in ordered_paths]
    return sorted(candidates, key=lambda item: (-item.score, item.path))


def _gate_matrix(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": "research_only_contract",
            "gate_name": "Research-only contract preserved",
            "severity": "critical",
            "passed": report["research_only"] is True and report["operational_authority"] is False,
            "evidence": "research_only=true; operational_authority=false",
        },
        {
            "gate_id": "runtime_discovery_explicit",
            "gate_name": "Runtime source discovery is opt-in",
            "severity": "critical",
            "passed": report["allow_runtime_read"] is False or report["runtime_discovery_explicitly_allowed"] is True,
            "evidence": f"allow_runtime_read={str(report['allow_runtime_read']).lower()}",
        },
        {
            "gate_id": "paper_source_selection_blocked",
            "gate_name": "Paper source is discovered but not selected operationally",
            "severity": "critical",
            "passed": report["paper_source_selected"] is False,
            "evidence": "paper_source_selected=false",
        },
        {
            "gate_id": "real_slice_computation_not_executed",
            "gate_name": "Real OOS slice computation remains separate",
            "severity": "high",
            "passed": report["real_slice_metrics_computed"] is False,
            "evidence": "real_slice_metrics_computed=false",
        },
        {
            "gate_id": "promotion_blocked",
            "gate_name": "Rule and model promotion blocked",
            "severity": "critical",
            "passed": report["can_promote_rules"] is False and report["can_promote_model"] is False,
            "evidence": "can_promote_rules=false; can_promote_model=false",
        },
        {
            "gate_id": "runtime_unchanged",
            "gate_name": "Runtime and execution surfaces unchanged",
            "severity": "critical",
            "passed": report["updates_freqtrade"] is False and report["sends_orders"] is False,
            "evidence": "no runtime updates; sends_orders=false",
        },
    ]


def _summarize_gates(gates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failed = [gate for gate in gates if gate.get("passed") is not True]
    return {
        "gate_count": len(gates),
        "passed_gate_count": len(gates) - len(failed),
        "failed_gate_count": len(failed),
        "failed_gate_ids": [str(gate["gate_id"]) for gate in failed],
        "critical_failed_gate_ids": [
            str(gate["gate_id"]) for gate in failed if gate.get("severity") == "critical"
        ],
    }


def build_paper_master_divergence_paper_source_discovery_report(
    *,
    project_root: str | Path = ".",
    allow_runtime_read: bool = False,
    candidate_sources: Sequence[str | Path] | None = None,
    discovery_roots: Sequence[str | Path] | None = None,
    max_files_per_root: int = 250,
) -> dict[str, Any]:
    """Build a blocked, research-only paper source discovery report."""
    root = Path(project_root).resolve()
    candidates = discover_paper_source_candidates(
        project_root=root,
        allow_runtime_read=allow_runtime_read,
        candidate_sources=candidate_sources,
        discovery_roots=discovery_roots,
        max_files_per_root=max_files_per_root,
    )
    public_candidates = [candidate.to_dict() for candidate in candidates]
    viable = [candidate for candidate in candidates if candidate.confidence in {"high", "medium"}]
    best = viable[0].to_dict() if viable else None

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "status": "blocked",
        "reason": "paper_source_discovery_requires_manual_review_before_real_oos_computation",
        "decision": DECISION,
        "project_root": str(project_root),
        "input_mode": "runtime_discovery_read_only" if allow_runtime_read else "no_runtime_discovery",
        "allow_runtime_read": allow_runtime_read,
        "runtime_discovery_explicitly_allowed": allow_runtime_read,
        "paper_source_discovery_created": True,
        "paper_source_candidates_discovered": bool(viable),
        "paper_source_candidate_count": len(viable),
        "candidate_count": len(candidates),
        "best_paper_source_candidate": best,
        "paper_source_selected": False,
        "paper_source_path": None,
        "paper_source_rows": None,
        "paper_source_sha256": None,
        "paper_source_schema_status": None,
        "manual_review_required": True,
        "ready_for_real_slice_computation": False,
        "real_slice_metrics_computed": False,
        "oos_slice_metrics_computed": False,
        "oos_validation_required": True,
        "oos_validated": False,
        "hypothesis_scope": HYPOTHESIS_SCOPE,
        "oos_slice_dimensions": OOS_SLICE_DIMENSIONS,
        "divergence_confirmed": True,
        "divergence_metrics": CANONICAL_DIVERGENCE_METRICS,
        "paper_replicates_master_edge": False,
        "source_candidates": public_candidates,
        "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        "default_discovery_roots": DEFAULT_DISCOVERY_ROOTS,
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "minimum_next_research_gates": [
            "revisar candidato paper manualmente",
            "executar real_slice_computation com paper-source explícito",
            "validar H1/H2/H6 por dia/símbolo/lado/exit_reason/duração",
            "bloquear qualquer regra que remova ROI winners materialmente",
            "exigir registry shadow bloqueado antes de qualquer observação paper",
        ],
        "write_requested": False,
        "write_performed": False,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_sqlite": False,
        "writes_parquet": False,
    }
    report.update(_safety_flags())
    gates = _gate_matrix(report)
    report["gate_matrix"] = gates
    report["gate_summary"] = _summarize_gates(gates)
    return _json_safe(report)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--allow-runtime-read", action="store_true")
    parser.add_argument("--candidate-source", action="append", default=[])
    parser.add_argument("--discovery-root", action="append", default=[])
    parser.add_argument("--max-files-per-root", type=int, default=250)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_paper_master_divergence_paper_source_discovery_report(
        project_root=args.project_root,
        allow_runtime_read=args.allow_runtime_read,
        candidate_sources=args.candidate_source,
        discovery_roots=args.discovery_root or None,
        max_files_per_root=args.max_files_per_root,
    )
    if args.no_write:
        report["write_requested"] = False
        report["write_performed"] = False
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
