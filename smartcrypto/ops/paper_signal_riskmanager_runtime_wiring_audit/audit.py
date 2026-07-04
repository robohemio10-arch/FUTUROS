"""Runtime wiring audit: RiskManager gate on the paper signal path.

Branch: codex/paper-signal-riskmanager-runtime-wiring-audit-v1

Background
----------
An external audit of the SMART FUTUROS handover found that the signal file
Freqtrade's paper strategy actually reads was written by paths that never
called ``RiskManager.approve()`` / ``RiskManager.approve_many()``. Instead
each writer stamped every candidate signal with a hardcoded
``risk_approved`` value, so RiskManager's approval/rejection decision never
had any effect on which signals reached Freqtrade.

This module re-verifies, with evidence rather than narrative, that the fix
(``smartcrypto/execution/signal_risk_gate.py``) is actually wired into every
writer Freqtrade's strategy reads from, and that the reader itself enforces
strict RiskManager approval. It performs two kinds of checks:

Static source checks
    Reads each writer file as text and confirms it imports and calls
    ``apply_risk_manager_gate`` and does not contain a hardcoded
    ``"risk_approved": True`` assignment of its own. Reads the strategy
    file and confirms its active-signal check is a strict ``is True``
    comparison, and that its fallback keeps searching subsequent signal
    files instead of stopping at the first file that merely exists.

Dynamic gate probes
    Actually calls ``apply_risk_manager_gate`` with (a) a RiskManager
    pointed at a missing config file, (b) a RiskManager stub that raises,
    and (c) a RiskManager stub that returns a mix of approved/rejected
    decisions - and asserts fail-closed / correct pass-through behavior in
    each case.

Known, explicitly out-of-scope limitation
    ``smartcrypto/execution/market_signal_exporter.py`` has the same
    historical hardcoded ``risk_approved=True`` pattern and is intentionally
    NOT covered by this branch's fix, because it is not wired into
    ``docker-compose.paper.yml``'s continuously-running services (only a
    standalone, manually invoked script uses it). This module reports that
    fact under ``known_limitations`` / ``evidence_gaps`` instead of hiding
    it.

This module does not send orders, does not access a private exchange
connection, does not change risk limits, does not change models, and does
not write to data/runtime, SQLite or Parquet. It only reads source files and
(via ``apply_risk_manager_gate``) ``config/risk_limits.yml``, and optionally
writes its own JSON/Markdown report under ``data/reports/``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.execution.signal_risk_gate import apply_risk_manager_gate
from smartcrypto.risk.risk_manager import SignalRiskDecision

SCHEMA_VERSION = "paper_signal_riskmanager_runtime_wiring_audit_v1"
BRANCH = "codex/paper-signal-riskmanager-runtime-wiring-audit-v1"
RISK_GATE_MODULE = "smartcrypto.execution.signal_risk_gate"
RISK_GATE_FUNCTION = "apply_risk_manager_gate"

DEFAULT_OUTPUT_REPORT = Path("data/reports/paper_signal_riskmanager_runtime_wiring_audit_v1.json")
DEFAULT_MARKDOWN_REPORT = Path("data/reports/paper_signal_riskmanager_runtime_wiring_audit_v1.md")

WRITER_MODULES: tuple[str, ...] = (
    "smartcrypto/execution/signal_producer.py",
    "smartcrypto/qlib_engine/signal_exporter.py",
    "smartcrypto/execution/signal_contract_guard.py",
)
READER_MODULE = "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"

# Documented, intentionally out-of-scope for this branch. See module
# docstring. Never silently dropped from the report.
KNOWN_OUT_OF_SCOPE_WRITERS: tuple[str, ...] = (
    "smartcrypto/execution/market_signal_exporter.py",
)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "live_behavior_changed": False,
    "canary_behavior_changed": False,
    "sends_orders": False,
    "exchange_private_access": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_risk_manager": False,
    "registry_write_performed": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
}

_HARDCODED_APPROVAL_PATTERN = re.compile(r'"risk_approved"\s*:\s*True')
_IMPORT_PATTERN = re.compile(
    r"from\s+smartcrypto\.execution\.signal_risk_gate\s+import\s*\(([^)]*)\)"
    r"|from\s+smartcrypto\.execution\.signal_risk_gate\s+import\s+([^\n]+)"
)
_STRICT_NOT_TRUE_PATTERN = re.compile(r'risk_approved"?\)\s*is\s+not\s+True')
_STRICT_IS_TRUE_PATTERN = re.compile(r'risk_approved"?\)\s*is\s+True')


@dataclass(frozen=True)
class WriterSourceCheck:
    """Static evidence for a single candidate-signal writer file."""

    relpath: str
    exists: bool
    sha256: str | None
    imports_risk_gate: bool
    calls_apply_risk_manager_gate: bool
    hardcoded_risk_approved_true_found: bool
    wired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "exists": self.exists,
            "sha256": self.sha256,
            "imports_risk_gate": self.imports_risk_gate,
            "calls_apply_risk_manager_gate": self.calls_apply_risk_manager_gate,
            "hardcoded_risk_approved_true_found": self.hardcoded_risk_approved_true_found,
            "wired": self.wired,
        }


@dataclass(frozen=True)
class ReaderSourceCheck:
    """Static evidence for the Freqtrade strategy that reads signals."""

    relpath: str
    exists: bool
    sha256: str | None
    strict_boolean_check_found: bool
    fallback_iterates_all_paths: bool
    wired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "exists": self.exists,
            "sha256": self.sha256,
            "strict_boolean_check_found": self.strict_boolean_check_found,
            "fallback_iterates_all_paths": self.fallback_iterates_all_paths,
            "wired": self.wired,
        }


class _RaisingRiskManager:
    """Test-only stub: simulates RiskManager raising during evaluation."""

    def approve_many(self, signals: list[dict[str, Any]]) -> list[SignalRiskDecision]:
        raise RuntimeError("simulated_risk_manager_failure_for_audit_probe")


class _MixedDecisionRiskManager:
    """Test-only stub: approves 'long' candidates, rejects everything else."""

    def approve_many(self, signals: list[dict[str, Any]]) -> list[SignalRiskDecision]:
        decisions: list[SignalRiskDecision] = []
        for signal in signals:
            approved = str(signal.get("side")) == "long"
            decisions.append(
                SignalRiskDecision(
                    approved=approved,
                    status="approved" if approved else "blocked",
                    reasons=["stub_long_only"] if approved else ["stub_rejects_non_long"],
                    signal=dict(signal),
                    created_at=_utc_now_iso(),
                )
            )
        return decisions


def audit_writer_source(root: Path, relpath: str) -> WriterSourceCheck:
    """Static, read-only inspection of a single candidate-signal writer."""

    path = (root / relpath).resolve()
    if not path.exists():
        return WriterSourceCheck(
            relpath=relpath,
            exists=False,
            sha256=None,
            imports_risk_gate=False,
            calls_apply_risk_manager_gate=False,
            hardcoded_risk_approved_true_found=False,
            wired=False,
        )
    text = path.read_text(encoding="utf-8")
    imports_gate = bool(_IMPORT_PATTERN.search(text)) and RISK_GATE_FUNCTION in text
    calls_gate = f"{RISK_GATE_FUNCTION}(" in text
    hardcoded = bool(_HARDCODED_APPROVAL_PATTERN.search(text))
    wired = imports_gate and calls_gate and not hardcoded
    return WriterSourceCheck(
        relpath=relpath,
        exists=True,
        sha256=_sha256_file(path),
        imports_risk_gate=imports_gate,
        calls_apply_risk_manager_gate=calls_gate,
        hardcoded_risk_approved_true_found=hardcoded,
        wired=wired,
    )


def audit_reader_source(root: Path, relpath: str = READER_MODULE) -> ReaderSourceCheck:
    """Static, read-only inspection of the Freqtrade strategy reader."""

    path = (root / relpath).resolve()
    if not path.exists():
        return ReaderSourceCheck(
            relpath=relpath,
            exists=False,
            sha256=None,
            strict_boolean_check_found=False,
            fallback_iterates_all_paths=False,
            wired=False,
        )
    text = path.read_text(encoding="utf-8")
    strict_check = bool(_STRICT_NOT_TRUE_PATTERN.search(text)) and bool(_STRICT_IS_TRUE_PATTERN.search(text))
    fallback_iterates = (
        "for path in self._signal_paths" in text
        and "last_reason" in text
        and "active_signals_found" in text
    )
    wired = strict_check and fallback_iterates
    return ReaderSourceCheck(
        relpath=relpath,
        exists=True,
        sha256=_sha256_file(path),
        strict_boolean_check_found=strict_check,
        fallback_iterates_all_paths=fallback_iterates,
        wired=wired,
    )


def _sample_candidates() -> list[dict[str, Any]]:
    return [
        {"pair": "ETH/USDT:USDT", "symbol": "ETHUSDT", "side": "long", "score": 0.8},
        {"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "short", "score": -0.7},
    ]


def _probe_missing_config(root: Path) -> dict[str, Any]:
    missing_path = root / "config" / "__nonexistent_risk_limits_for_audit_probe__.yml"
    result = apply_risk_manager_gate(_sample_candidates(), risk_limits_path=missing_path)
    passed = (
        result.status == "blocked"
        and result.risk_manager_available is False
        and result.signals_approved == 0
        and len(result.approved_signals) == 0
        and all(signal.get("risk_approved") is False for signal in result.rejected_signals)
    )
    return {"passed": passed, "status": result.status, "reason": result.reason}


def _probe_raising_risk_manager() -> dict[str, Any]:
    result = apply_risk_manager_gate(_sample_candidates(), risk_manager=_RaisingRiskManager())
    passed = (
        result.status == "blocked"
        and result.signals_approved == 0
        and len(result.approved_signals) == 0
        and all(signal.get("risk_approved") is False for signal in result.rejected_signals)
    )
    return {"passed": passed, "status": result.status, "reason": result.reason}


def _probe_mixed_decisions() -> dict[str, Any]:
    result = apply_risk_manager_gate(_sample_candidates(), risk_manager=_MixedDecisionRiskManager())
    approved_sides = sorted({str(signal.get("side")) for signal in result.approved_signals})
    rejected_sides = sorted({str(signal.get("side")) for signal in result.rejected_signals})
    passed = (
        result.status == "ok"
        and result.signals_approved == 1
        and result.signals_rejected == 1
        and approved_sides == ["long"]
        and rejected_sides == ["short"]
        and all(signal.get("risk_approved") is True for signal in result.approved_signals)
        and all(signal.get("risk_approved") is False for signal in result.rejected_signals)
    )
    return {
        "passed": passed,
        "status": result.status,
        "approved_sides": approved_sides,
        "rejected_sides": rejected_sides,
    }


def run_gate_probes(root: Path) -> dict[str, dict[str, Any]]:
    """Exercise ``apply_risk_manager_gate`` directly against controlled inputs."""

    return {
        "fail_closed_on_missing_config": _probe_missing_config(root),
        "fail_closed_on_risk_manager_exception": _probe_raising_risk_manager(),
        "approved_and_rejected_signals_handled_correctly": _probe_mixed_decisions(),
    }


def build_audit_report(
    *,
    project_root: str | Path,
    write: bool = False,
    no_write: bool = True,
    output_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the RiskManager runtime-wiring audit report."""

    root = Path(project_root).resolve()
    write_requested = bool(write and not no_write)

    writer_checks = [audit_writer_source(root, relpath) for relpath in WRITER_MODULES]
    reader_check = audit_reader_source(root, READER_MODULE)
    known_limitations = [audit_writer_source(root, relpath) for relpath in KNOWN_OUT_OF_SCOPE_WRITERS]
    gate_probes = run_gate_probes(root)

    all_writers_wired = all(check.wired for check in writer_checks)
    reader_wired = reader_check.wired
    gate_probes_pass = all(bool(probe.get("passed")) for probe in gate_probes.values())

    if not all_writers_wired:
        status, reason = "blocked", "writer_not_wired_to_risk_manager_gate"
    elif not reader_wired:
        status, reason = "blocked", "reader_does_not_enforce_strict_risk_approval"
    elif not gate_probes_pass:
        status, reason = "blocked", "risk_manager_gate_probe_failed"
    else:
        status, reason = "ok", "paper_signal_riskmanager_runtime_wiring_confirmed"

    evidence_gaps: list[str] = []
    if any(check.exists and check.hardcoded_risk_approved_true_found for check in known_limitations):
        evidence_gaps.append(
            "market_signal_exporter_not_wired_to_risk_manager_gate_out_of_scope_for_this_branch"
        )

    report: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "project_root": str(root),
        "branch": BRANCH,
        "risk_gate_module": RISK_GATE_MODULE,
        "risk_gate_function": RISK_GATE_FUNCTION,
        "writer_checks": [check.to_dict() for check in writer_checks],
        "reader_check": reader_check.to_dict(),
        "all_writers_wired": all_writers_wired,
        "reader_wired": reader_wired,
        "gate_probes": gate_probes,
        "gate_probes_pass": gate_probes_pass,
        "known_limitations": [check.to_dict() for check in known_limitations],
        "evidence_gaps": evidence_gaps,
        "safety_flags": dict(SAFETY_FLAGS),
        "write_requested": write_requested,
        "write_performed": False,
        "output_path": None,
        "markdown_output_path": None,
        "validation_errors": [],
    }
    report["validation_errors"] = validate_audit_report(report)
    if report["validation_errors"] and report["status"] == "ok":
        report["status"] = "blocked"
        report["reason"] = "audit_report_schema_validation_failed"

    if write_requested:
        output_path = _resolve_path(root, output_report, DEFAULT_OUTPUT_REPORT)
        markdown_path = _resolve_path(root, markdown_report, DEFAULT_MARKDOWN_REPORT)
        output_error = _validate_output_path(root, output_path, suffix=".json")
        markdown_error = _validate_output_path(root, markdown_path, suffix=".md")
        if output_error is not None or markdown_error is not None:
            report["status"] = "blocked"
            report["reason"] = output_error or markdown_error
            return report
        output_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        report["write_performed"] = True
        report["output_path"] = _project_relative(output_path, root)
        report["markdown_output_path"] = _project_relative(markdown_path, root)
    return report


def validate_audit_report(report: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    required_fields = (
        "status",
        "reason",
        "schema_version",
        "generated_at_utc",
        "project_root",
        "branch",
        "writer_checks",
        "reader_check",
        "all_writers_wired",
        "reader_wired",
        "gate_probes",
        "gate_probes_pass",
        "known_limitations",
        "evidence_gaps",
        "safety_flags",
        "write_performed",
    )
    for field_name in required_fields:
        if field_name not in report:
            errors.append(f"missing_required_field:{field_name}")
    safety_flags = report.get("safety_flags")
    for key, expected in SAFETY_FLAGS.items():
        if not isinstance(safety_flags, Mapping) or safety_flags.get(key) is not expected:
            errors.append(f"safety_flags.{key}_must_be_{str(expected).lower()}")
    return sorted(set(errors))


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# Paper Signal RiskManager Runtime Wiring Audit V1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- All writers wired: `{report.get('all_writers_wired')}`",
        f"- Reader wired: `{report.get('reader_wired')}`",
        f"- Gate probes pass: `{report.get('gate_probes_pass')}`",
        "",
        "## Writers",
        "",
    ]
    for check in report.get("writer_checks", []):
        lines.append(
            f"- `{check.get('relpath')}`: wired=`{check.get('wired')}`, "
            f"hardcoded_risk_approved_true_found=`{check.get('hardcoded_risk_approved_true_found')}`"
        )
    lines += [
        "",
        "## Reader",
        "",
        f"- `{report.get('reader_check', {}).get('relpath')}`: wired=`{report.get('reader_wired')}`",
        "",
        "## Known limitations (explicitly out of scope for this branch)",
        "",
    ]
    for check in report.get("known_limitations", []):
        lines.append(
            f"- `{check.get('relpath')}`: wired=`{check.get('wired')}` "
            "(not part of this branch's fix scope; see module docstring)"
        )
    lines += [
        "",
        "This audit is read-only and paper-only. It does not send orders, does not access a "
        "private exchange connection, does not change risk limits, does not change models, "
        "and does not write to data/runtime, SQLite or Parquet.",
        "",
    ]
    return "\n".join(lines)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _resolve_path(root: Path, value: str | Path | None, default: str | Path) -> Path:
    path = Path(value) if value is not None else Path(default)
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
