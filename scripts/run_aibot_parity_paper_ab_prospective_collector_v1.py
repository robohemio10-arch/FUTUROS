#!/usr/bin/env python3
"""Collect prospective AIBOT Parity Paper A/B evidence from explicit sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT_TEXT = str(_PROJECT_ROOT)
if _PROJECT_ROOT_TEXT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_TEXT)

from smartcrypto.research.aibot_parity_paper_ab_prospective_collector import (  # noqa: E402
    DEFAULT_OBSERVATIONS,
    DEFAULT_PAPER_CONFIG,
    DEFAULT_STRATEGY_FILE,
    DEFAULT_STRATEGY_NAME,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    build_paper_financial_config_fingerprint,
    collect_prospective_evidence,
    immutable_assignment_rows,
    load_aibot_snapshots,
    load_decision_ledger_jsonl,
    load_normalized_closed_trades,
    read_observation_ledger,
    write_observations_idempotent,
)
from smartcrypto.research.aibot_parity_paper_ab_prospective_collector.runtime_foundation import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG,
    load_runtime_foundation_config,
)
from smartcrypto.research.aibot_parity_paper_ab_soak import load_preregistration  # noqa: E402
from smartcrypto.research.paper_ab_edge_selector.persistence import (  # noqa: E402
    resolve_assignments_path,
    resolve_report_path,
    write_assignments_idempotent,
    write_report,
)

DEFAULT_CONFIG = Path("config/research/aibot_parity_paper_ab_soak_v1.json")
DEFAULT_REPORT = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_collector_v1.json"
)
DEFAULT_ASSIGNMENTS = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_soak_assignments_v1.jsonl"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect decision-time AIBOT observations and later Paper closed outcomes "
            "for the research-only prospective A/B soak."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--runtime-foundation-config",
        default=str(DEFAULT_RUNTIME_CONFIG),
        help=(
            "Static runtime-foundation config containing the preregistered "
            "financial fingerprint."
        ),
    )
    parser.add_argument("--paper-config", default=str(DEFAULT_PAPER_CONFIG))
    parser.add_argument("--strategy-file", default=str(DEFAULT_STRATEGY_FILE))
    parser.add_argument("--strategy-name", default=DEFAULT_STRATEGY_NAME)
    parser.add_argument(
        "--aibot-snapshot-json",
        help="Explicit AIBOT snapshot or snapshot collection used for new observation capture.",
    )
    parser.add_argument(
        "--decision-ledger-jsonl",
        help=(
            "Explicit read-only Decision Ledger 4.2 JSONL source; no runtime default "
            "is assumed."
        ),
    )
    parser.add_argument(
        "--closed-trades-path",
        help=(
            "Explicit authorized read-only Paper closed-trades export; no runtime "
            "default is assumed."
        ),
    )
    parser.add_argument(
        "--allow-paper-runtime-read",
        action="store_true",
        help="Permit reads of the explicitly supplied Decision Ledger/closed-trades sources.",
    )
    parser.add_argument(
        "--assert-financial-config-unchanged",
        action="store_true",
        help=(
            "Backward-compatible operator assertion retained as audit metadata only. "
            "The machine-readable financial fingerprint is authoritative."
        ),
    )
    parser.add_argument("--observations-jsonl", default=str(DEFAULT_OBSERVATIONS))
    parser.add_argument("--assignments-jsonl", default=str(DEFAULT_ASSIGNMENTS))
    parser.add_argument("--output-json", default=str(DEFAULT_REPORT))
    parser.add_argument("--write-observations", action="store_true")
    parser.add_argument("--write-assignments", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _blocked_report(reason: str, *, error_type: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "error_type": error_type,
        "decision": "COLLECT_PROSPECTIVE_EVIDENCE",
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "collection_clock_reason": (
            "software_execution_alone_does_not_prove_paper_host_recurring_collection"
        ),
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
        "write_requested": False,
        "write_performed": False,
        "write_report_performed": False,
        "write_observations_performed": False,
        "write_assignments_performed": False,
        "observations_appended": 0,
        "assignments_appended": 0,
    }


def _safe_reason(exc: Exception) -> str:
    first_line = str(exc).splitlines()[0].strip()
    return first_line[:300] if first_line else type(exc).__name__


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.project_root).resolve()
    config_path = _resolve(root, args.config)
    runtime_config_path = _resolve(root, args.runtime_foundation_config)
    observations_path = _resolve(root, args.observations_jsonl)
    snapshot_path = (
        _resolve(root, args.aibot_snapshot_json) if args.aibot_snapshot_json else None
    )
    ledger_path = (
        _resolve(root, args.decision_ledger_jsonl)
        if args.decision_ledger_jsonl
        else None
    )
    closed_trades_path = (
        _resolve(root, args.closed_trades_path) if args.closed_trades_path else None
    )

    preregistration = load_preregistration(config_path)
    runtime_config = load_runtime_foundation_config(
        project_root=root,
        path=runtime_config_path,
    )
    fingerprint = build_paper_financial_config_fingerprint(
        project_root=root,
        paper_config_path=args.paper_config,
        strategy_path=args.strategy_file,
        strategy_name=args.strategy_name,
    )
    expected_fingerprint = runtime_config.expected_financial_config_sha256
    fingerprint_matches = (
        fingerprint.paper_financial_config_sha256 == expected_fingerprint
    )

    existing_observations = read_observation_ledger(observations_path)
    snapshots = load_aibot_snapshots(snapshot_path) if snapshot_path is not None else []

    if snapshot_path is not None and ledger_path is None:
        raise ValueError("decision_ledger_required_for_new_observation_capture")
    if (
        ledger_path is not None or closed_trades_path is not None
    ) and not args.allow_paper_runtime_read:
        raise ValueError("paper_runtime_read_requires_explicit_allow")

    decisions = ()
    trade_links = ()
    if ledger_path is not None:
        ledger = load_decision_ledger_jsonl(ledger_path)
        decisions = ledger.decisions
        trade_links = ledger.trade_links

    closed_trades: list[dict[str, Any]] = []
    closed_trade_diagnostics: dict[str, Any] = {
        "source_status": "not_requested",
        "normalized_closed_trade_count": 0,
    }
    if closed_trades_path is not None:
        closed_trades, closed_trade_diagnostics = load_normalized_closed_trades(
            project_root=root,
            source_path=closed_trades_path,
        )

    captured_at = datetime.now(UTC)
    collector_run_id = f"collector-run-{uuid.uuid4().hex}"
    result = collect_prospective_evidence(
        preregistration=preregistration,
        snapshots=snapshots,
        decisions=decisions,
        trade_links=trade_links,
        closed_trades=closed_trades,
        existing_observations=existing_observations,
        financial_config_unchanged=fingerprint_matches,
        paper_financial_config_sha256=fingerprint.paper_financial_config_sha256,
        expected_financial_config_sha256=expected_fingerprint,
        captured_at_utc=captured_at,
        collector_run_id=collector_run_id,
    )
    report = result.report
    report["inputs"] = {
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "runtime_foundation_config_path": str(runtime_config_path),
        "runtime_foundation_config_sha256": _sha256_file(runtime_config_path),
        "aibot_snapshot_path": None if snapshot_path is None else str(snapshot_path),
        "aibot_snapshot_sha256": _sha256_file(snapshot_path),
        "decision_ledger_path": None if ledger_path is None else str(ledger_path),
        "decision_ledger_sha256": _sha256_file(ledger_path),
        "closed_trades_path": (
            None if closed_trades_path is None else str(closed_trades_path)
        ),
        "closed_trades_sha256": _sha256_file(closed_trades_path),
        "observation_ledger_path": str(observations_path),
        "financial_config_unchanged_asserted": bool(
            args.assert_financial_config_unchanged
        ),
        "financial_config_fingerprint": fingerprint.to_dict(),
        "expected_financial_config_sha256": expected_fingerprint,
        "financial_config_fingerprint_valid": fingerprint_matches,
        "paper_runtime_read_allowed": bool(args.allow_paper_runtime_read),
        "closed_trade_diagnostics": closed_trade_diagnostics,
    }

    write_requested = bool(
        args.write_observations or args.write_assignments or args.write_report
    )
    report["write_requested"] = write_requested
    report["write_report_performed"] = False
    report["write_observations_performed"] = False
    report["write_assignments_performed"] = False

    # Evidence rows are persisted only when collector integrity is clean. A blocked
    # run may still persist its diagnostic report when explicitly requested.
    if report["status"] == "ok" and args.write_observations:
        appended = write_observations_idempotent(
            project_root=root,
            path=observations_path,
            observations=result.observations,
        )
        report["observations_appended"] = appended
        report["write_observations_performed"] = appended > 0

    if report["status"] == "ok" and args.write_assignments:
        assignments_path = resolve_assignments_path(root, args.assignments_jsonl)
        appended = write_assignments_idempotent(
            root,
            assignments_path,
            immutable_assignment_rows(result.assignments),
        )
        report["assignments_appended"] = appended
        report["write_assignments_performed"] = appended > 0

    report["write_performed"] = bool(
        report.get("write_observations_performed")
        or report.get("write_assignments_performed")
    )

    if args.write_report:
        report_path = resolve_report_path(root, args.output_json)
        report["write_report_performed"] = True
        report["write_performed"] = True
        write_report(root, report_path, report)

    return report


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        report = _blocked_report(_safe_reason(exc), error_type=type(exc).__name__)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    else:
        print(
            f"status={report.get('status')} reason={report.get('reason')} "
            f"collection_clock_started={report.get('collection_clock_started')}"
        )
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
