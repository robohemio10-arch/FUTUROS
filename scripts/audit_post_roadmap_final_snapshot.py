from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "post_roadmap_final_consolidation_snapshot_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/post_roadmap_final_consolidation_snapshot.json")
SNAPSHOT_DOC = Path("docs/POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md")

REQUIRED_DOCS = (
    Path("docs/MANUAL_GO_NO_GO_LIVE_CANARY_GOVERNANCE.md"),
    Path("docs/LIVE_CANARY_CONTRACT_WITH_HARD_BLOCKS.md"),
    Path("docs/SAAS_TENANT_SECURITY_BASELINE.md"),
    SNAPSHOT_DOC,
)
REQUIRED_SCRIPTS = (
    Path("scripts/audit_manual_go_no_go_governance.py"),
    Path("scripts/audit_live_canary_contract.py"),
    Path("scripts/audit_saas_tenant_security_baseline.py"),
)
REQUIRED_OPS_MODULES = (
    Path("smartcrypto/ops/manual_go_no_go_governance.py"),
    Path("smartcrypto/ops/live_canary_contract.py"),
    Path("smartcrypto/ops/saas_tenant_security_baseline.py"),
)
REQUIRED_TESTS = (
    Path("tests/test_manual_go_no_go_live_canary_governance.py"),
    Path("tests/test_live_canary_contract_with_hard_blocks.py"),
    Path("tests/test_saas_tenant_security_baseline.py"),
)

ROADMAP_BRANCHES = (
    "canonical-30d-soak-readiness-threshold-enforcement",
    "transitive-lock-docker-runtime-reproducibility",
    "zip-standalone-audit-fallback",
    "runtime-evidence-pack-and-readiness-snapshot-v2",
    "paper-shadow-soak-continuity-and-gap-accounting",
    "monte-carlo-no-trade-recovery-diagnostics",
    "ai-shadow-threshold-live-readiness-evidence",
    "manual-go-no-go-live-canary-governance",
    "live-canary-contract-with-hard-blocks",
    "saas-tenant-security-baseline",
)
REQUIRED_INVARIANTS = (
    "paper_only=true",
    "shadow_only=true",
    "live_release_allowed=false",
    "canary_release_allowed=false",
    "release_allowed=false",
    "real_order_submission_enabled=false",
    "order_submission_enabled=false",
    "exchange_private_access=false",
    "sends_orders=false",
    "changes_risk=false",
)


@dataclass(frozen=True)
class SnapshotAuditResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_post_roadmap_final_snapshot_audit(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    no_write: bool = False,
    now: datetime | None = None,
) -> SnapshotAuditResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    current_time = now or datetime.now(timezone.utc)

    required_paths = (*REQUIRED_DOCS, *REQUIRED_SCRIPTS, *REQUIRED_OPS_MODULES, *REQUIRED_TESTS)
    missing_paths = [str(path) for path in required_paths if not (root / path).is_file()]

    snapshot_text = ""
    doc_validation_errors: list[str] = []
    snapshot_path = root / SNAPSHOT_DOC
    if snapshot_path.is_file():
        snapshot_text = snapshot_path.read_text(encoding="utf-8")
        doc_validation_errors.extend(validate_snapshot_doc(snapshot_text))
    else:
        doc_validation_errors.append(f"missing_snapshot_doc:{SNAPSHOT_DOC}")

    blocking_reasons = [f"missing_path:{path}" for path in missing_paths] + doc_validation_errors
    status = "ok" if not blocking_reasons else "blocked"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "required_docs": [str(path) for path in REQUIRED_DOCS],
        "required_scripts": [str(path) for path in REQUIRED_SCRIPTS],
        "required_ops_modules": [str(path) for path in REQUIRED_OPS_MODULES],
        "required_tests": [str(path) for path in REQUIRED_TESTS],
        "roadmap_branches": list(ROADMAP_BRANCHES),
        "required_invariants": list(REQUIRED_INVARIANTS),
        "missing_paths": missing_paths,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "paper_only": True,
        "shadow_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "release_allowed": False,
        "real_order_submission_enabled": False,
        "order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "changes_model": False,
        "promotes_model": False,
        "runtime_logic_changed": False,
        "documentation_only_snapshot": True,
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return SnapshotAuditResult(report=report, output_path=output_path, write_performed=write_performed)


def validate_snapshot_doc(text: str) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()
    for branch in ROADMAP_BRANCHES:
        if branch.lower() not in lowered:
            errors.append(f"snapshot_doc_missing_branch:{branch}")
    for invariant in REQUIRED_INVARIANTS:
        if invariant.lower() not in lowered:
            errors.append(f"snapshot_doc_missing_invariant:{invariant}")
    if "não autoriza live trading" not in lowered:
        errors.append("snapshot_doc_missing_no_live_authorization_statement")
    if "não altera lógica runtime" not in lowered:
        errors.append("snapshot_doc_missing_no_runtime_logic_change_statement")
    return sorted(set(errors))


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
    parser = argparse.ArgumentParser(description="Audita snapshot final pós-roadmap 9/10.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_post_roadmap_final_snapshot_audit(
        project_root=Path(args.project_root),
        output=Path(args.output),
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
                    "documentation_only_snapshot": result.report["documentation_only_snapshot"],
                    "runtime_logic_changed": result.report["runtime_logic_changed"],
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
