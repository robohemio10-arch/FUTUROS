from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "current_project_handover_after_ntfy_telegram_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/current_project_handover_audit_report.json")

REQUIRED_VERSIONED_FILES = (
    Path("docs/CANONICAL_SOURCE_OF_TRUTH_INDEX.md"),
    Path("docs/CURRENT_PROJECT_HANDOVER_AFTER_NTFY_TELEGRAM.md"),
    Path("docs/POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md"),
    Path("docs/NTFY_TELEGRAM_CRITICAL_NOTIFICATIONS.md"),
    Path("docs/FINANCIAL_EVENT_LOG_AND_ALERTING.md"),
    Path("docs/LIVE_CANARY_CONTRACT_WITH_HARD_BLOCKS.md"),
    Path("docs/MANUAL_GO_NO_GO_LIVE_CANARY_GOVERNANCE.md"),
    Path("docs/SAAS_TENANT_SECURITY_BASELINE.md"),
    Path("PROJECT_MANIFEST_CLEAN.json"),
    Path("smartcrypto/ops/notification_channels.py"),
    Path("scripts/run_critical_notification_dispatch.py"),
    Path("tests/test_notification_channels.py"),
)

REQUIRED_HANDOVER_MARKERS = (
    "e18c6a1cbdcba9e864ed53cc0f55ee1f5f923e3b",
    "PR #125",
    "ntfy-telegram-critical-notifications",
    "codex/zip-standalone-dynamic-import-audit-fix",
    "codex/critical-notifications-dashboard-panel",
    "paper_only=true",
    "shadow_only=true",
    "sends_orders=false",
    "changes_risk=false",
    "exchange_private_access=false",
)

REQUIRED_SOURCE_OF_TRUTH_MARKERS = (
    "repositório Git",
    "docs canônicos versionados",
    "PROJECT_MANIFEST_CLEAN.json",
    "data/reports",
    "handover técnico atualizado",
)

SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "runtime_logic_changed": False,
    "dashboard_changed": False,
}


@dataclass(frozen=True)
class HandoverAuditResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_current_project_handover_audit(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    no_write: bool = False,
    now: datetime | None = None,
) -> HandoverAuditResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    current_time = now or datetime.now(timezone.utc)

    missing_files = [str(path) for path in REQUIRED_VERSIONED_FILES if not (root / path).is_file()]
    blocking_reasons = [f"missing_file:{path}" for path in missing_files]

    handover_path = root / "docs/CURRENT_PROJECT_HANDOVER_AFTER_NTFY_TELEGRAM.md"
    source_index_path = root / "docs/CANONICAL_SOURCE_OF_TRUTH_INDEX.md"

    handover_text = read_text_if_exists(handover_path)
    source_index_text = read_text_if_exists(source_index_path)

    for marker in REQUIRED_HANDOVER_MARKERS:
        if marker.lower() not in handover_text.lower():
            blocking_reasons.append(f"handover_missing_marker:{marker}")

    for marker in REQUIRED_SOURCE_OF_TRUTH_MARKERS:
        if marker.lower() not in source_index_text.lower():
            blocking_reasons.append(f"source_index_missing_marker:{marker}")

    status = "ok" if not blocking_reasons else "blocked"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "required_versioned_files": [str(path) for path in REQUIRED_VERSIONED_FILES],
        "required_handover_markers": list(REQUIRED_HANDOVER_MARKERS),
        "required_source_of_truth_markers": list(REQUIRED_SOURCE_OF_TRUTH_MARKERS),
        "missing_files": missing_files,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "source_of_truth_order": [
            "git_repository_dev",
            "versioned_canonical_docs",
            "PROJECT_MANIFEST_CLEAN.json",
            "data/reports_runtime_evidence_when_applicable",
            "updated_technical_handover",
        ],
        **SAFETY_FLAGS,
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return HandoverAuditResult(report=report, output_path=output_path, write_performed=write_performed)


def read_text_if_exists(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audita handover técnico atual do projeto FUTUROS.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_current_project_handover_audit(
        project_root=args.project_root,
        output=args.output,
        no_write=args.no_write,
    )

    if args.json:
        print(json.dumps(result.report, indent=2, sort_keys=True))
    else:
        print(
            json.dumps(
                {
                    "status": result.report["status"],
                    "output": str(result.output_path),
                    "write_performed": result.write_performed,
                    "paper_only": result.report["paper_only"],
                    "shadow_only": result.report["shadow_only"],
                    "sends_orders": result.report["sends_orders"],
                    "changes_risk": result.report["changes_risk"],
                    "blocking_reasons_count": len(result.report["blocking_reasons"]),
                },
                sort_keys=True,
            )
        )

    return 0 if result.report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
