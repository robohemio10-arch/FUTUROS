from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = Path("data/reports/market_features_runtime_writer_audit.json")
OPERATIONAL_ARTIFACT = "market_features_60d.parquet"
LOOKAHEAD_TOKEN = "future_ret_"

RUNTIME_WRITER_MARKERS = (
    "write_operational_market_features",
    "build_market_features(",
    "refresh_qlib_market_features(",
    "run_phase22_feature_build(",
    "merge_with_main_features(",
)
RUNTIME_READER_MARKERS = (
    "read_parquet",
    "pd.read_parquet",
    "describe_parquet",
    "inspect_market_feature_source",
    "market_features_path",
)
OFFLINE_MARKERS = (
    "walkforward",
    "backtest",
    "training",
    "dataset",
    "label",
    "target",
    "sidecar",
    "baseline",
)
ALLOWED_RUNTIME_GUARD_MARKERS = (
    "write_operational_market_features",
    "build_market_features(",
    "refresh_qlib_market_features(",
    "refresh_qlib_market_features",
    "run_paper_refresh_supervisor(",
    "run_qlib_paper_refresh_supervisor(",
    "sanitize_operational_market_features",
)


@dataclass(frozen=True)
class WriterAuditEntry:
    path: str
    classification: str
    references_market_features_60d: bool
    references_future_ret: bool
    to_parquet_count: int
    uses_guard: bool
    reason: str


def iter_python_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
            continue
        if root.exists():
            files.extend(sorted(path for path in root.rglob("*.py") if path.is_file()))
    return sorted(set(files), key=lambda path: str(path).lower())


def audit_market_features_runtime_writers(
    *,
    roots: Iterable[Path] | None = None,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    project_root: str | Path = PROJECT_ROOT,
) -> dict:
    root = Path(project_root).resolve()
    scan_roots = [Path(item) for item in (roots or [root / "scripts", root / "smartcrypto"])]
    entries = [_classify_file(path, root) for path in iter_python_files(scan_roots)]

    runtime_writer_files = _paths_by_class(entries, "runtime_writer")
    runtime_reader_files = _paths_by_class(entries, "runtime_reader")
    offline_label_files = _paths_by_class(entries, "offline_training_or_label_writer")
    test_only_files = _paths_by_class(entries, "test_only")
    unknown_files = _paths_by_class(entries, "unknown")
    allowed_runtime_writers = [
        entry.path
        for entry in entries
        if entry.classification == "runtime_writer" and entry.uses_guard
    ]
    prohibited_runtime_writers = [
        entry.path
        for entry in entries
        if entry.classification == "runtime_writer" and not entry.uses_guard
    ]

    status = "ok"
    reason = "ok"
    if prohibited_runtime_writers:
        status = "blocked"
        reason = "prohibited_runtime_writer_detected"
    elif unknown_files:
        status = "blocked"
        reason = "unknown_market_features_reference_detected"

    report = {
        "status": status,
        "reason": reason,
        "scanned_files": len(entries),
        "runtime_writer_files": runtime_writer_files,
        "runtime_reader_files": runtime_reader_files,
        "offline_label_files": offline_label_files,
        "test_only_files": test_only_files,
        "unknown_files": unknown_files,
        "prohibited_runtime_writers": prohibited_runtime_writers,
        "allowed_runtime_writers": allowed_runtime_writers,
        "entries": [asdict(entry) for entry in entries if _is_relevant(entry)],
        "paper_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    target = Path(report_path)
    if not target.is_absolute():
        target = root / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _paths_by_class(entries: list[WriterAuditEntry], classification: str) -> list[str]:
    return sorted(
        entry.path
        for entry in entries
        if entry.classification == classification and _is_relevant(entry)
    )


def _classify_file(path: Path, project_root: Path) -> WriterAuditEntry:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    rel_path = _relative_path(path, project_root)
    lowered_path = rel_path.lower().replace("\\", "/")
    lowered_text = text.lower()

    references_market_features = OPERATIONAL_ARTIFACT in text
    references_future_ret = LOOKAHEAD_TOKEN in text
    to_parquet_count = text.count(".to_parquet(")
    uses_guard = any(marker in text for marker in ALLOWED_RUNTIME_GUARD_MARKERS)
    has_writer_marker = any(marker in text for marker in RUNTIME_WRITER_MARKERS)
    has_reader_marker = any(marker in text for marker in RUNTIME_READER_MARKERS)
    has_direct_operational_write = _has_direct_operational_parquet_write(text)
    future_label_writer = references_future_ret and to_parquet_count > 0

    if lowered_path.endswith("scripts/audit_market_features_runtime_writers.py"):
        classification = "runtime_reader"
        reason = "audit_tool"
    elif lowered_path.startswith("tests/"):
        classification = "test_only"
        reason = "test_path"
    elif references_market_features and (
        has_direct_operational_write
        or has_writer_marker
        or lowered_path.endswith("scripts/sanitize_market_features_lookahead.py")
        or "market_features_output_path" in text
    ):
        classification = "runtime_writer"
        reason = "operational_artifact_writer_reference"
    elif references_market_features and _is_offline_or_label_context(lowered_path, lowered_text):
        classification = "offline_training_or_label_writer"
        reason = "offline_training_or_label_context"
    elif references_market_features:
        classification = "runtime_reader" if has_reader_marker else "unknown"
        reason = "operational_artifact_reader_reference" if has_reader_marker else "unclassified_operational_reference"
    elif future_label_writer and _is_offline_or_label_context(lowered_path, lowered_text):
        classification = "offline_training_or_label_writer"
        reason = "future_return_offline_label_writer"
    elif uses_guard:
        classification = "runtime_writer"
        reason = "central_operational_writer_or_guard"
    else:
        classification = "not_relevant"
        reason = "not_relevant_to_operational_artifact"

    return WriterAuditEntry(
        path=rel_path,
        classification=classification,
        references_market_features_60d=references_market_features,
        references_future_ret=references_future_ret,
        to_parquet_count=to_parquet_count,
        uses_guard=uses_guard,
        reason=reason,
    )


def _is_offline_or_label_context(path: str, text: str) -> bool:
    return any(marker in path or marker in text for marker in OFFLINE_MARKERS)


def _is_relevant(entry: WriterAuditEntry) -> bool:
    return (
        entry.references_market_features_60d
        or entry.references_future_ret
        or entry.to_parquet_count > 0
        or entry.uses_guard
        or entry.classification in {"unknown", "runtime_writer"}
    )


def _has_direct_operational_parquet_write(text: str) -> bool:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if ".to_parquet(" not in line:
            continue
        window = "\n".join(lines[max(0, index - 4) : min(len(lines), index + 5)])
        if OPERATIONAL_ARTIFACT in window:
            return True
    return False


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit runtime writers of data/features/market_features_60d.parquet."
    )
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Root/file to scan. Defaults to scripts/ and smartcrypto/.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    roots = [Path(item) for item in args.roots] if args.roots else None
    report = audit_market_features_runtime_writers(roots=roots, report_path=args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
