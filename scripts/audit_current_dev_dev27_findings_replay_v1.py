#!/usr/bin/env python3
"""Replay DEV27 findings against the current dev branch.

This is an audit-only tool. It performs static, read-only checks to decide
whether the historical DEV27 findings still appear in the current repository:

* CLI scripts with direct ``smartcrypto`` imports and no explicit standalone
  project-root bootstrap.
* Dashboard/Streamlit code paths that appear able to perform external
  notification side effects.
* Runtime safety audit readiness signals that should be reviewed before any
  hardening branch.

The tool intentionally does not fix files, execute schedulers, train models,
write runtime artifacts, send notifications, or interact with exchanges.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "current_dev_audit_replay_dev27_findings_v1"
PROJECT_NAME = "SMART FUTUROS"
DECISION = "AUDIT_ONLY_NO_CODE_FIX"

FORBIDDEN_OUTPUT_PARTS = {
    "data",
    "runtime",
    "reports",
    "logs",
    "freqtrade",
    ".git",
    "user_data",
}

DASHBOARD_SCAN_DIRS = (
    Path("smartcrypto/dashboard/pages"),
    Path("smartcrypto/dashboard/components"),
    Path("smartcrypto/dashboard/controls"),
    Path("smartcrypto/dashboard/ui"),
    Path("smartcrypto/dashboard/services"),
)

SCRIPT_SCAN_DIR = Path("scripts")

SMARTCRYPTO_IMPORT_RE = re.compile(r"^\s*(?:from\s+smartcrypto(?:\.|\s+import)|import\s+smartcrypto(?:\.|\s|$))", re.MULTILINE)
MAIN_GUARD_RE = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")
BOOTSTRAP_MARKERS = (
    "sys.path.insert",
    "sys.path.append",
    "Path(__file__).resolve().parents",
    "Path(__file__).resolve().parent.parent",
    "PYTHONPATH",
    "site.addsitedir",
    "bootstrap_project_root",
    "ensure_project_root",
)

# Exact tokens that indicate potential real dashboard side effects. The audit is
# static and deliberately conservative; findings require manual review.
DASHBOARD_CRITICAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("requests_post", re.compile(r"\brequests\.post\s*\(", re.IGNORECASE)),
    ("httpx_post", re.compile(r"\bhttpx\.post\s*\(", re.IGNORECASE)),
    ("aiohttp_client_session", re.compile(r"\baiohttp\.ClientSession\s*\(", re.IGNORECASE)),
    ("urllib_request_urlopen", re.compile(r"\burllib\.request\.urlopen\s*\(", re.IGNORECASE)),
)

DASHBOARD_HIGH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dry_run_false", re.compile(r"\bdry_run\s*=\s*False\b")),
    ("send_real", re.compile(r"\bsend_real\b", re.IGNORECASE)),
    ("real_notification", re.compile(r"\breal_notification\b", re.IGNORECASE)),
    ("telegram_token_reference", re.compile(r"\b(?:TELEGRAM|telegram).{0,40}(?:TOKEN|token)\b")),
    ("ntfy_endpoint_reference", re.compile(r"\b(?:NTFY|ntfy).{0,40}(?:URL|url|TOPIC|topic)\b")),
)

RUNTIME_SAFETY_CANDIDATES = (
    Path("scripts/validate_runtime_safety_config.py"),
    Path("scripts/audit_runtime_safety_config.py"),
    Path("config/runtime_safety.paper.yml"),
    Path("config/runtime_safety.yml"),
)


@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: str
    file: str
    line: int
    finding_type: str
    message: str
    evidence: str
    branch_00b_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "finding_type": self.finding_type,
            "message": self.message,
            "evidence": self.evidence,
            "branch_00b_candidate": self.branch_00b_candidate,
        }


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _line_number(text: str, match_start: int) -> int:
    return text.count("\n", 0, match_start) + 1


def _trim_evidence(line: str, max_chars: int = 160) -> str:
    compact = " ".join(line.strip().split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3] + "..."


def _iter_python_files(base: Path) -> Iterable[Path]:
    if not base.exists():
        return []
    return sorted(
        path
        for path in base.rglob("*.py")
        if path.is_file()
        and "__pycache__" not in path.parts
        and not path.name.endswith(".pyc")
    )


def _has_standalone_bootstrap(text: str) -> bool:
    return any(marker in text for marker in BOOTSTRAP_MARKERS)


def audit_cli_standalone(project_root: Path) -> dict[str, Any]:
    """Statically audit direct script standalone risk.

    A script is considered at-risk when it is executable-like, imports
    ``smartcrypto`` directly, and does not show an explicit project-root
    bootstrap. This is a warning-level signal unless the file name suggests the
    historical selector class of scripts, in which case it becomes a candidate
    for Branch 00B review.
    """

    scripts_root = project_root / SCRIPT_SCAN_DIR
    findings: list[Finding] = []
    scanned_files = 0
    smartcrypto_importing_scripts = 0
    executable_like_scripts = 0

    for path in _iter_python_files(scripts_root):
        if path.name == Path(__file__).name:
            # The audit script intentionally contains smartcrypto-related text.
            continue
        rel = _project_relative(path, project_root)
        text = _read_text(path)
        scanned_files += 1

        has_smartcrypto_import = bool(SMARTCRYPTO_IMPORT_RE.search(text))
        has_main_guard = bool(MAIN_GUARD_RE.search(text))
        has_bootstrap = _has_standalone_bootstrap(text)
        if has_smartcrypto_import:
            smartcrypto_importing_scripts += 1
        if has_main_guard:
            executable_like_scripts += 1

        if has_smartcrypto_import and has_main_guard and not has_bootstrap:
            selector_like = "selector" in path.name.lower() or "freqtrade" in path.name.lower()
            severity = "high" if selector_like else "medium"
            message = (
                "Executable-like script imports smartcrypto without an explicit standalone "
                "project-root bootstrap. Direct `python scripts/<name>.py` execution may "
                "depend on package installation or PYTHONPATH."
            )
            findings.append(
                Finding(
                    finding_id=f"cli_standalone_{len(findings) + 1:03d}",
                    severity=severity,
                    file=rel,
                    line=1,
                    finding_type="missing_explicit_project_root_bootstrap",
                    message=message,
                    evidence="smartcrypto import + __main__ guard + no bootstrap marker",
                    branch_00b_candidate=selector_like,
                )
            )

    high_count = sum(1 for finding in findings if finding.severity == "high")
    medium_count = sum(1 for finding in findings if finding.severity == "medium")
    branch_00b_required = any(f.branch_00b_candidate for f in findings)
    status = "blocked" if branch_00b_required else ("warning" if findings else "ok")

    return {
        "status": status,
        "scanned_files": scanned_files,
        "smartcrypto_importing_scripts": smartcrypto_importing_scripts,
        "executable_like_scripts": executable_like_scripts,
        "finding_count": len(findings),
        "high_count": high_count,
        "medium_count": medium_count,
        "branch_00b_required": branch_00b_required,
        "findings": [finding.to_dict() for finding in findings],
    }


def audit_dashboard_notifications(project_root: Path) -> dict[str, Any]:
    """Statically audit dashboard notification side-effect risk."""

    findings: list[Finding] = []
    scanned_files = 0
    scanned_dirs: list[str] = []

    for rel_dir in DASHBOARD_SCAN_DIRS:
        base = project_root / rel_dir
        if not base.exists():
            continue
        scanned_dirs.append(rel_dir.as_posix())
        for path in _iter_python_files(base):
            rel = _project_relative(path, project_root)
            text = _read_text(path)
            scanned_files += 1
            lines = text.splitlines()

            for pattern_name, pattern in DASHBOARD_CRITICAL_PATTERNS:
                for match in pattern.finditer(text):
                    line_no = _line_number(text, match.start())
                    line_text = lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else ""
                    findings.append(
                        Finding(
                            finding_id=f"dashboard_notification_{len(findings) + 1:03d}",
                            severity="critical",
                            file=rel,
                            line=line_no,
                            finding_type=pattern_name,
                            message=(
                                "Dashboard code contains a direct external network side-effect "
                                "primitive. Streamlit/dashboard code must remain read-only and "
                                "must not send real notifications."
                            ),
                            evidence=_trim_evidence(line_text),
                            branch_00b_candidate=True,
                        )
                    )

            for pattern_name, pattern in DASHBOARD_HIGH_PATTERNS:
                for match in pattern.finditer(text):
                    line_no = _line_number(text, match.start())
                    line_text = lines[line_no - 1] if 0 <= line_no - 1 < len(lines) else ""
                    findings.append(
                        Finding(
                            finding_id=f"dashboard_notification_{len(findings) + 1:03d}",
                            severity="high",
                            file=rel,
                            line=line_no,
                            finding_type=pattern_name,
                            message=(
                                "Dashboard code contains a token associated with real notification "
                                "or dry-run bypass risk. Manual review is required."
                            ),
                            evidence=_trim_evidence(line_text),
                            branch_00b_candidate=True,
                        )
                    )

    critical_count = sum(1 for finding in findings if finding.severity == "critical")
    high_count = sum(1 for finding in findings if finding.severity == "high")
    branch_00b_required = critical_count > 0 or high_count > 0
    status = "blocked" if critical_count else ("warning" if high_count else "ok")

    return {
        "status": status,
        "scanned_dirs": scanned_dirs,
        "scanned_files": scanned_files,
        "finding_count": len(findings),
        "critical_count": critical_count,
        "high_count": high_count,
        "branch_00b_required": branch_00b_required,
        "findings": [finding.to_dict() for finding in findings],
    }


def audit_runtime_safety_presence(project_root: Path) -> dict[str, Any]:
    """Collect runtime safety audit presence signals without executing runtime."""

    present = []
    missing = []
    for rel_path in RUNTIME_SAFETY_CANDIDATES:
        path = project_root / rel_path
        if path.exists():
            present.append(rel_path.as_posix())
        else:
            missing.append(rel_path.as_posix())

    strict_command_candidates = []
    if (project_root / "scripts/validate_runtime_safety_config.py").exists():
        strict_command_candidates.append(
            "python scripts/validate_runtime_safety_config.py --project-root . --environment paper --strict --json"
        )
    if (project_root / "scripts/audit_runtime_safety_config.py").exists():
        strict_command_candidates.append(
            "python scripts/audit_runtime_safety_config.py --project-root . --json"
        )

    status = "ok" if present else "warning"
    return {
        "status": status,
        "executed": False,
        "reason": "static_presence_only_no_runtime_execution",
        "present_artifacts": present,
        "missing_artifacts": missing,
        "strict_command_candidates": strict_command_candidates,
        "branch_00b_required": False,
    }


def build_replay_audit(project_root: str | Path = ".") -> dict[str, Any]:
    root = Path(project_root)
    cli = audit_cli_standalone(root)
    dashboard = audit_dashboard_notifications(root)
    runtime_safety = audit_runtime_safety_presence(root)

    critical_conditions = dashboard["status"] == "blocked"
    branch_00b_required = bool(
        cli.get("branch_00b_required")
        or dashboard.get("branch_00b_required")
    )

    warning_conditions = any(
        section.get("status") == "warning"
        for section in (cli, dashboard, runtime_safety)
    )

    if critical_conditions or branch_00b_required:
        status = "blocked"
    elif warning_conditions:
        status = "warning"
    else:
        status = "ok"

    gate_matrix = [
        {
            "gate_id": "audit_only_no_fix",
            "gate_name": "Audit does not apply fixes",
            "severity": "critical",
            "passed": True,
            "evidence": "fixes_applied=false; write_performed=false by default",
        },
        {
            "gate_id": "cli_standalone_replay",
            "gate_name": "CLI standalone DEV27 replay completed",
            "severity": "high",
            "passed": cli["status"] in {"ok", "warning", "blocked"},
            "evidence": f"scanned_files={cli['scanned_files']}; findings={cli['finding_count']}",
        },
        {
            "gate_id": "dashboard_notification_replay",
            "gate_name": "Dashboard notification DEV27 replay completed",
            "severity": "critical",
            "passed": dashboard["status"] != "blocked",
            "evidence": (
                f"critical_count={dashboard['critical_count']}; "
                f"high_count={dashboard['high_count']}; scanned_files={dashboard['scanned_files']}"
            ),
        },
        {
            "gate_id": "runtime_safety_presence",
            "gate_name": "Runtime safety audit artifacts located",
            "severity": "high",
            "passed": runtime_safety["status"] == "ok",
            "evidence": f"present={len(runtime_safety['present_artifacts'])}; executed=false",
        },
        {
            "gate_id": "branch_00b_decision_available",
            "gate_name": "Branch 00B decision is explicit",
            "severity": "critical",
            "passed": True,
            "evidence": f"branch_00b_required={str(branch_00b_required).lower()}",
        },
    ]
    failed_gate_ids = [gate["gate_id"] for gate in gate_matrix if not gate["passed"]]
    critical_failed_gate_ids = [
        gate["gate_id"]
        for gate in gate_matrix
        if not gate["passed"] and gate["severity"] == "critical"
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "project_name": PROJECT_NAME,
        "project_root": str(project_root),
        "status": status,
        "reason": "current_dev_dev27_findings_replayed_audit_only",
        "decision": DECISION,
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "release_authority": False,
        "fixes_applied": False,
        "write_requested": False,
        "write_performed": False,
        "changes_runtime": False,
        "changes_risk": False,
        "changes_model": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "registers_scheduler": False,
        "executes_scheduler": False,
        "executes_orchestrator": False,
        "executes_stage_builders": False,
        "runs_training": False,
        "applies_shadow_rules": False,
        "applies_feedback_to_ai_shadow": False,
        "can_promote_model": False,
        "can_promote_rules": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "writes_data": False,
        "writes_runtime": False,
        "writes_reports": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "input_mode": "static_source_replay_no_runtime_rows_loaded",
        "cli_standalone_findings": cli,
        "dashboard_notification_findings": dashboard,
        "runtime_safety_findings": runtime_safety,
        "branch_00b_required": branch_00b_required,
        "branch_00b_reason": _branch_00b_reason(cli, dashboard, branch_00b_required),
        "gate_matrix": gate_matrix,
        "gate_summary": {
            "gate_count": len(gate_matrix),
            "passed_gate_count": len(gate_matrix) - len(failed_gate_ids),
            "failed_gate_count": len(failed_gate_ids),
            "failed_gate_ids": failed_gate_ids,
            "critical_failed_gate_ids": critical_failed_gate_ids,
        },
        "forbidden_actions": [
            "corrigir arquivos nesta branch",
            "alterar Freqtrade",
            "alterar RiskManager",
            "alterar Qlib runtime",
            "alterar IA Shadow runtime",
            "alterar modelos",
            "alterar datasets operacionais",
            "registrar scheduler",
            "executar scheduler",
            "executar orquestrador",
            "executar stage builders",
            "enviar Telegram real",
            "enviar NTFY real",
            "usar exchange privada",
            "habilitar live",
            "habilitar canary",
            "enviar ordem real",
            "promover modelo",
            "promover regra",
            "aplicar candidate rule",
            "aplicar feedback IA Shadow",
            "escrever artefatos em data/runtime/reports/logs/freqtrade",
        ],
        "allowed_next_steps": [
            "se branch_00b_required=true abrir hardening cirurgico separado",
            "se branch_00b_required=false fechar auditoria como replay documental",
            "executar baseline pos-merge sem alterar runtime",
        ],
        "validation_errors": [],
    }


def _branch_00b_reason(cli: dict[str, Any], dashboard: dict[str, Any], required: bool) -> str:
    if not required:
        return "no_confirmed_dev27_hardening_required_by_static_replay"
    reasons: list[str] = []
    if cli.get("branch_00b_required"):
        reasons.append("cli_standalone_high_priority_findings")
    if dashboard.get("branch_00b_required"):
        reasons.append("dashboard_notification_side_effect_risk")
    return "+".join(reasons)


def _is_forbidden_output_path(path: Path, project_root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(project_root.resolve()).parts
    except ValueError:
        return False
    lowered = {part.lower() for part in rel_parts}
    return bool(lowered & FORBIDDEN_OUTPUT_PARTS)


def write_payload_if_requested(payload: dict[str, Any], output: str | None, project_root: Path, no_write: bool) -> dict[str, Any]:
    if no_write or not output:
        payload["write_requested"] = bool(output)
        payload["write_performed"] = False
        payload["output_path"] = None if not output else output
        payload["cli_reason"] = "no_write_requested" if no_write else "no_output_requested"
        return payload

    output_path = Path(output)
    if _is_forbidden_output_path(output_path, project_root):
        payload["write_requested"] = True
        payload["write_performed"] = False
        payload["output_path"] = str(output_path)
        payload["status"] = "blocked"
        payload["cli_reason"] = "forbidden_output_path"
        payload["validation_errors"] = payload.get("validation_errors", []) + [
            "output_path_under_forbidden_runtime_or_data_directory"
        ]
        return payload

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["write_requested"] = True
    payload["write_performed"] = True
    payload["output_path"] = str(output_path)
    payload["cli_reason"] = "explicit_output_written"
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay DEV27 audit findings against current dev.")
    parser.add_argument("--project-root", default=".", help="Project root to audit.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--no-write", action="store_true", help="Do not write output files.")
    parser.add_argument("--output", default=None, help="Optional explicit output path outside forbidden runtime/data dirs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root)
    payload = build_replay_audit(project_root=args.project_root)
    payload = write_payload_if_requested(payload, args.output, project_root, no_write=args.no_write)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"status={payload['status']}")
        print(f"decision={payload['decision']}")
        print(f"branch_00b_required={str(payload['branch_00b_required']).lower()}")
        print(f"reason={payload['branch_00b_reason']}")

    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    raise SystemExit(main())
