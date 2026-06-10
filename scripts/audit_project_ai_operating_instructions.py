from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "project_ai_operating_instructions_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/project_ai_operating_instructions_audit.json")

REQUIRED_FILES = (
    Path("docs/PROJECT_AI_OPERATING_INSTRUCTIONS.md"),
    Path("docs/PROJECT_AI_NEW_CHAT_BOOTSTRAP_PROMPT.md"),
    Path("PROJECT_MANIFEST_CLEAN.json"),
)

REQUIRED_MARKERS = (
    "maximizar lucro líquido esperado",
    "menor risco",
    "Qlib",
    "IA Shadow",
    "RiskManager",
    "Freqtrade",
    "paper_only=true",
    "shadow_only=true",
    "live_trading_enabled=false",
    "order_submission_enabled=false",
    "real_order_submission_enabled=false",
    "exchange_private_access=false",
    "sends_orders=false",
    "changes_risk=false",
    "FeatureContract",
    "ModelRegistry",
    "DriftMonitor",
    "walk-forward",
    "Monte Carlo",
    "expectancy líquido",
    "codex/fix-standalone-manifest-and-runtime-evidence-closeout",
    "codex/critical-notifications-dashboard-panel",
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
    "model_promoted": False,
    "training_dataset_changed": False,
}


@dataclass(frozen=True)
class ProjectAiOperatingInstructionsAuditResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_project_ai_operating_instructions_audit(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    no_write: bool = False,
    now: datetime | None = None,
) -> ProjectAiOperatingInstructionsAuditResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    current_time = now or datetime.now(timezone.utc)

    missing_files = [str(path) for path in REQUIRED_FILES if not (root / path).is_file()]
    blocking_reasons: list[str] = [f"missing_file:{path}" for path in missing_files]

    instructions_path = root / "docs/PROJECT_AI_OPERATING_INSTRUCTIONS.md"
    prompt_path = root / "docs/PROJECT_AI_NEW_CHAT_BOOTSTRAP_PROMPT.md"

    combined_text = "\n".join(
        [
            read_text_if_exists(instructions_path),
            read_text_if_exists(prompt_path),
        ]
    ).lower()

    for marker in REQUIRED_MARKERS:
        if marker.lower() not in combined_text:
            blocking_reasons.append(f"missing_marker:{marker}")

    status = "ok" if not blocking_reasons else "blocked"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "required_files": [str(path) for path in REQUIRED_FILES],
        "required_markers": list(REQUIRED_MARKERS),
        "missing_files": missing_files,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "purpose": "auditar instrucoes operacionais da IA do projeto FUTUROS",
        **SAFETY_FLAGS,
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return ProjectAiOperatingInstructionsAuditResult(
        report=report,
        output_path=output_path,
        write_performed=write_performed,
    )


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
    parser = argparse.ArgumentParser(description="Audita instrucoes operacionais da IA do projeto FUTUROS.")
    parser.add_argument("--project-root", default=".", help="Raiz do projeto FUTUROS.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = build_project_ai_operating_instructions_audit(
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
