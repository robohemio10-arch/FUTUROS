"""Static fail-closed audit for shared runtime report writers."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "runtime_shared_report_writer_audit_v2"

SHARED_RUNTIME_TARGETS = (
    "data/reports/trade_event_notifications_report.json",
    "data/reports/freqtrade_paper_db_snapshot_export.json",
    "data/reports/phase14_runtime_feedback_sync_report.json",
    "data/reports/phase14_open_positions_report.json",
    "data/reports/phase14_closed_feedback_report.json",
    "data/reports/phase14_output_summary.json",
    "data/reports/phase14_summary.json",
    "data/reports/qlib_paper_refresh_supervisor_report.json",
    "data/reports/qlib_market_features_refresh_report.json",
    "data/reports/qlib_fresh_prediction_runner_report.json",
    "data/reports/phase13_signal_producer_report.json",
    "data/reports/phase13_summary.json",
    "data/reports/ai_shadow_model_decisions.jsonl",
    "data/reports/ai_shadow_model_decision_logger_report.json",
    "data/reports/ai_shadow_model_outcomes.jsonl",
    "data/reports/ai_shadow_model_outcomes_report.json",
    "data/reports/ai_shadow_financial_threshold_evaluation_report.json",
    "data/reports/monte_carlo_risk_simulation_report.json",
    "data/reports/dashboard_real_paper_sources_snapshot.json",
    "data/reports/paper_candidate_filter_runtime_observation_pack_v1.json",
    "data/reports/paper_candidate_filter_runtime_observation_pack_v1.md",
    "data/reports/runtime_evidence_pack_v2.json",
    "data/reports/readiness_snapshot_v2.json",
    "data/reports/phase16_summary.json",
    "data/reports/phase17_summary.json",
)

SHARED_WRITER_MODULES = (
    "scripts/collect_phase16_summary.py",
    "scripts/collect_phase17_summary.py",
    "scripts/export_freqtrade_paper_db_snapshot.py",
    "smartcrypto/data/paper_trade_lifecycle.py",
    "smartcrypto/ml/ai_shadow_financial_evaluation.py",
    "smartcrypto/ml/monte_carlo_risk_simulation.py",
    "smartcrypto/execution/signal_producer.py",
    "smartcrypto/execution/signal_store.py",
    "smartcrypto/ml/model_decision_logger.py",
    "smartcrypto/ml/outcome_tracker.py",
    "smartcrypto/ops/dashboard_real_paper_sources/builder.py",
    (
        "smartcrypto/ops/paper_candidate_filter_runtime_observation_pack/"
        "observation_pack.py"
    ),
    "smartcrypto/ops/runtime_evidence_pack.py",
    "smartcrypto/ops/trade_event_notifications.py",
    "smartcrypto/qlib_engine/common.py",
    "smartcrypto/qlib_engine/paper_refresh_supervisor.py",
)

INSTITUTIONAL_CALLS = frozenset(
    {
        "atomic_append_jsonl",
        "atomic_write_json",
        "atomic_write_text",
    }
)


@dataclass(frozen=True)
class WriterAuthority:
    path: str
    function_or_class: str
    operation: str
    authority_id: str
    justification: str


AUTHORITIES = (
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_atomic_replace_bytes_locked",
        operation="fdopen_write",
        authority_id="atomic_runtime_writer_v2.tempfile_write",
        justification="exclusive same-filesystem temporary owned by the invocation",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_atomic_replace_bytes_locked",
        operation="fsync_callable",
        authority_id="atomic_runtime_writer_v2.file_durability",
        justification="injected os.fsync-compatible durability before promotion",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_atomic_replace_bytes_locked",
        operation="mkstemp_callable",
        authority_id="atomic_runtime_writer_v2.exclusive_tempfile",
        justification="injected tempfile.mkstemp-compatible same-directory creator",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_replace_with_retry",
        operation="replace_callable",
        authority_id="atomic_runtime_writer_v2.atomic_promotion",
        justification="bounded retry around the injected os.replace-compatible call",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_fsync_parent_directory",
        operation="os.open",
        authority_id="atomic_runtime_writer_v2.parent_directory_open",
        justification="read-only descriptor used exclusively for parent fsync",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_fsync_parent_directory",
        operation="fsync_callable",
        authority_id="atomic_runtime_writer_v2.parent_directory_durability",
        justification="injected os.fsync-compatible parent directory durability",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_InterProcessFileLock.acquire",
        operation="os.open",
        authority_id="atomic_runtime_writer_v2.lock_open",
        justification="exact per-target serialization lock",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_InterProcessFileLock.acquire",
        operation="os.write",
        authority_id="atomic_runtime_writer_v2.lock_initialization",
        justification="one-byte lock region initialization",
    ),
    WriterAuthority(
        path=(
            "smartcrypto/runtime/integrity_traceability_v2/"
            "atomic_writer.py"
        ),
        function_or_class="_InterProcessFileLock.acquire",
        operation="os.fsync",
        authority_id="atomic_runtime_writer_v2.lock_durability",
        justification="durable lock-file initialization",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_backup_to_owned_tempfile",
        operation="open_write",
        authority_id="phase14_snapshot.sqlite_backup_destination",
        justification="SQLite backup writes only its exclusive snapshot temporary",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_backup_to_owned_tempfile",
        operation="fsync_callable",
        authority_id="phase14_snapshot.sqlite_backup_durability",
        justification="fsync of the exclusive SQLite backup temporary",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_create_owned_tempfile",
        operation="mkstemp_callable",
        authority_id="phase14_snapshot.exclusive_tempfile",
        justification="same-directory temporary for certified SQLite snapshot export",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_promote_owned_tempfile",
        operation="replace_callable",
        authority_id="phase14_snapshot.atomic_promotion",
        justification="certified SQLite snapshot promotion, not a JSON report writer",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_fsync_parent_directory",
        operation="os.open",
        authority_id="phase14_snapshot.parent_directory_open",
        justification="read-only descriptor for snapshot parent fsync",
    ),
    WriterAuthority(
        path="scripts/export_freqtrade_paper_db_snapshot.py",
        function_or_class="_fsync_parent_directory",
        operation="fsync_callable",
        authority_id="phase14_snapshot.parent_directory_durability",
        justification="fsync after certified SQLite snapshot promotion",
    ),
)


@dataclass(frozen=True)
class WriterFinding:
    severity: Literal["critical", "high", "medium", "low"]
    path: str
    function_or_class: str
    operation: str
    line: int
    reason: str


def audit_runtime_shared_report_writers(
    project_root: str | Path,
) -> dict[str, Any]:
    root = Path(project_root).resolve(strict=False)
    findings: list[WriterFinding] = []
    scanned_files: list[str] = []

    for base in (root / "scripts", root / "smartcrypto"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            source = path.read_text(encoding="utf-8")
            if not _is_relevant(relative, source):
                continue
            scanned_files.append(relative)
            findings.extend(
                audit_source_text(
                    source,
                    path=relative,
                    require_shared_target_binding=not _is_declared_writer(
                        relative
                    ),
                )
            )

    critical_count = sum(item.severity == "critical" for item in findings)
    high_count = sum(item.severity == "high" for item in findings)
    status = "blocked" if critical_count or high_count else "ok"
    reason = (
        "direct_shared_runtime_writer_detected"
        if status == "blocked"
        else "institutional_writer_boundary_enforced"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "generated_at_utc": _utc_now(),
        "scanned_file_count": len(scanned_files),
        "scanned_files": scanned_files,
        "shared_runtime_targets": list(SHARED_RUNTIME_TARGETS),
        "authority_registry": [asdict(item) for item in AUTHORITIES],
        "authority_registry_exact_match_only": True,
        "wildcard_authority_allowed": False,
        "directory_authority_allowed": False,
        "critical_count": critical_count,
        "high_count": high_count,
        "medium_count": sum(item.severity == "medium" for item in findings),
        "low_count": sum(item.severity == "low" for item in findings),
        "findings": [asdict(item) for item in findings],
        "policy_documented": True,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "live_trading_enabled": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "automatic_promotion_allowed": False,
        "publishes_active_signals": False,
        "writes_financial_ledger": False,
        "writes_runtime": False,
    }


def audit_source_text(
    source: str,
    *,
    path: str,
    require_shared_target_binding: bool = False,
) -> list[WriterFinding]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [
            WriterFinding(
                severity="critical",
                path=path,
                function_or_class="<module>",
                operation="parse",
                line=1,
                reason="writer_source_syntax_invalid",
            )
        ]
    visitor = _WriterVisitor(
        path=path,
        shared_target_names=_shared_target_names(tree),
        require_shared_target_binding=require_shared_target_binding,
    )
    visitor.visit(tree)
    return visitor.findings


class _WriterVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        shared_target_names: frozenset[str],
        require_shared_target_binding: bool,
    ) -> None:
        self.path = path
        self.shared_target_names = shared_target_names
        self.require_shared_target_binding = require_shared_target_binding
        self.functions: list[str] = []
        self.classes: list[str] = []
        self.findings: list[WriterFinding] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        operation = _direct_write_operation(node)
        targets_shared = (
            not self.require_shared_target_binding
            or _call_references_shared_target(node, self.shared_target_names)
        )
        if (
            operation is not None
            and targets_shared
            and not self._authorized(operation)
        ):
            self.findings.append(
                WriterFinding(
                    severity="high",
                    path=self.path,
                    function_or_class=self._scope(),
                    operation=operation,
                    line=int(node.lineno),
                    reason="direct_shared_runtime_write_outside_exact_authority",
                )
            )
        self.generic_visit(node)

    def _scope(self) -> str:
        function = self.functions[-1] if self.functions else "<module>"
        if self.classes:
            return f"{self.classes[-1]}.{function}"
        return function

    def _authorized(self, operation: str) -> bool:
        scope = self._scope()
        return any(
            authority.path == self.path
            and authority.function_or_class == scope
            and authority.operation == operation
            for authority in AUTHORITIES
        )


def _is_relevant(path: str, source: str) -> bool:
    if _is_declared_writer(path):
        return True
    return any(target in source for target in SHARED_RUNTIME_TARGETS)


def _is_declared_writer(path: str) -> bool:
    if path in SHARED_WRITER_MODULES:
        return True
    return path == (
        "smartcrypto/runtime/integrity_traceability_v2/"
        "atomic_writer.py"
    )


def _shared_target_names(tree: ast.AST) -> frozenset[str]:
    bindings: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            if value is None or not _expression_references_shared_target(
                value,
                frozenset(bindings),
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in bindings:
                    bindings.add(target.id)
                    changed = True

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            defaults: list[ast.expr | None] = [None] * (
                len(positional) - len(node.args.defaults)
            )
            defaults.extend(node.args.defaults)
            for argument, default in zip(positional, defaults):
                if default is not None and _expression_references_shared_target(
                    default,
                    frozenset(bindings),
                ):
                    if argument.arg not in bindings:
                        bindings.add(argument.arg)
                        changed = True
    return frozenset(bindings)


def _call_references_shared_target(
    node: ast.Call,
    shared_target_names: frozenset[str],
) -> bool:
    candidates: list[ast.AST] = [*node.args]
    candidates.extend(keyword.value for keyword in node.keywords)
    if isinstance(node.func, ast.Attribute):
        candidates.append(node.func.value)
    return any(
        _expression_references_shared_target(candidate, shared_target_names)
        for candidate in candidates
    )


def _expression_references_shared_target(
    node: ast.AST,
    shared_target_names: frozenset[str],
) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in shared_target_names:
            return True
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            normalized = child.value.replace("\\", "/")
            if normalized in SHARED_RUNTIME_TARGETS:
                return True
    return False


def _direct_write_operation(node: ast.Call) -> str | None:
    name = _call_name(node.func)
    if name in INSTITUTIONAL_CALLS:
        return None
    if name in {"write_text", "write_bytes"}:
        return name
    if name.endswith(".write_text"):
        return "write_text"
    if name.endswith(".write_bytes"):
        return "write_bytes"
    if name == "os.replace":
        return "os.replace"
    if isinstance(node.func, ast.Name) and name == "replace":
        return "replace_callable"
    if name in {"replace"} and _looks_like_path_replace(node):
        return "path.replace"
    if name.endswith(".replace") and _looks_like_path_replace(node):
        return "path.replace"
    if name == "tempfile.mkstemp":
        return name
    if isinstance(node.func, ast.Name) and name == "mkstemp":
        return "mkstemp_callable"
    if name == "os.open":
        return name
    if name == "os.write":
        return name
    if name == "os.fsync":
        return name
    if isinstance(node.func, ast.Name) and name == "fsync":
        return "fsync_callable"
    if name == "fdopen" or name == "os.fdopen":
        if _mode_is_writable(node, positional_index=1):
            return "fdopen_write"
    if name == "open":
        if _mode_is_writable(node, positional_index=1):
            return "open_write"
    if name.endswith(".open"):
        if _mode_is_writable(node, positional_index=0):
            return "open_write"
    if name == "json.dump":
        return "json.dump"
    return None


def _mode_is_writable(node: ast.Call, *, positional_index: int) -> bool:
    mode: str | None = None
    if len(node.args) > positional_index:
        mode = _literal_string(node.args[positional_index])
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = _literal_string(keyword.value)
    return bool(mode and any(marker in mode for marker in ("w", "a", "x", "+")))


def _looks_like_path_replace(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or len(node.args) != 1:
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Call):
        return _call_name(receiver.func) in {"Path", "pathlib.Path"}
    if isinstance(receiver, ast.Name):
        name = receiver.id.lower()
        return any(
            marker in name
            for marker in ("path", "target", "temporary", "tempfile")
        )
    return False


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
