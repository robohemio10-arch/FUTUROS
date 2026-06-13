from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


POLICY_PATH = "docs/STATE_EXECUTION_LEDGER_BOUNDARY_AUDIT_V1.md"
SCHEMA_VERSION = "1.0"
SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}
SEVERITY_RANK = {"ok": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
WRITE_METHODS = {
    "to_csv",
    "to_excel",
    "to_json",
    "to_parquet",
    "write_bytes",
    "write_text",
}
READ_METHODS = {"load", "read", "read_bytes", "read_text"}
SQL_WRITE_PREFIXES = ("alter ", "create ", "delete ", "drop ", "insert ", "replace ", "update ")
DOMAIN_ROOTS = {"state", "execution", "ops", "risk", "dashboard"}
KNOWN_AUTHORITIES = {
    "smartcrypto/state/state_repository.py": "canonical_runtime_state_repository",
    "smartcrypto/state/repository.py": "tabular_state_repository",
    "smartcrypto/state/financial_event_log.py": "canonical_runtime_financial_event_log",
    "smartcrypto/state/audit_logger.py": "state_audit_log",
    "smartcrypto/state/capital_reservation_ledger.py": "state_capital_reservation_ledger",
    "smartcrypto/state/reconciliation_guard.py": "state_reconciliation_and_report",
    "smartcrypto/execution/capital_reservation_ledger.py": "sqlite_capital_reservation_ledger",
    "smartcrypto/execution/order_intent_ledger.py": "sqlite_order_intent_ledger",
    "smartcrypto/execution/signal_store.py": "paper_signal_store",
    "smartcrypto/execution/signal_exporter.py": "paper_signal_exporter",
    "smartcrypto/execution/market_signal_exporter.py": "paper_market_signal_exporter",
    "smartcrypto/execution/signal_producer.py": "paper_signal_producer",
    "smartcrypto/execution/paper_cycle_reset.py": "paper_cycle_state_maintenance",
    "smartcrypto/execution/paper_exit_control.py": "paper_exit_control_artifact",
    "smartcrypto/execution/paper_force_close.py": "paper_force_close_artifact",
    "smartcrypto/ops/financial_event_log.py": "operational_financial_evidence_log",
    "smartcrypto/dashboard/command_bus.py": "readonly_command_audit_log",
    "smartcrypto/risk/kill_switch_guard.py": "canonical_kill_switch_state",
    "smartcrypto/risk/risk_manager.py": "legacy_kill_switch_state_adapter",
    "smartcrypto/risk/paper_risk_controller.py": "paper_risk_controller_artifact",
    "smartcrypto/risk/monte_carlo_risk_budget_policy.py": "risk_policy_report_writer",
    "smartcrypto/risk/risk_recovery_modes.py": "risk_recovery_report_writer",
    "scripts/run_order_intent_capital_ledger_audit.py": "ledger_audit_report_writer",
}
AUTHORITY_MAP = {
    "state": {
        "authority": "persistent runtime state, reconciliation state, and canonical state event log",
        "allowed_dependencies": ["risk safety contracts"],
        "write_policy": "only named state repositories, ledgers, and audit log modules",
    },
    "ledger": {
        "authority": "order intent, capital reservation, and financial event history",
        "allowed_dependencies": ["state persistence primitives", "paper safety contracts"],
        "write_policy": "only explicit ledger implementations and their atomic repositories",
    },
    "execution": {
        "authority": "paper execution intents and paper signal artifacts",
        "allowed_dependencies": ["risk decisions", "state and ledger interfaces"],
        "write_policy": "paper artifacts only; no undeclared state or ledger ownership",
    },
    "ops": {
        "authority": "reports, snapshots, evidence, health, and audit outputs",
        "allowed_dependencies": ["read-only state, ledger, execution, and risk observations"],
        "write_policy": "reports/evidence only; operational state requires a named authority",
    },
    "risk": {
        "authority": "risk decisions and safety gates",
        "allowed_dependencies": ["state event logging"],
        "write_policy": "no implicit ownership of execution or financial ledgers",
    },
    "dashboard": {
        "authority": "read-only snapshots and explicitly stubbed controls",
        "allowed_dependencies": ["ops snapshots", "documented readonly command audit adapter"],
        "write_policy": "read-only except the named command audit log boundary",
    },
    "scripts": {
        "authority": "typed operational adapters and report entry points",
        "allowed_dependencies": ["public domain APIs"],
        "write_policy": "reports/evidence only unless delegating to a named authority",
    },
}


def load_versioned_file_discovery() -> ModuleType:
    module_name = "smartcrypto.ops.versioned_file_discovery"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"smartcrypto", "smartcrypto.ops", module_name}:
            raise
    path = Path(__file__).resolve().parents[1] / "smartcrypto" / "ops" / "versioned_file_discovery.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot_load_standalone_module:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


_DISCOVERY = load_versioned_file_discovery()
discover_versioned_files = _DISCOVERY.discover_versioned_files


def normalize_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").strip("/")


def domain_for_path(relative_path: str) -> str:
    parts = normalize_path(relative_path).split("/")
    if len(parts) >= 2 and parts[0] == "smartcrypto" and parts[1] in DOMAIN_ROOTS:
        if "ledger" in parts[-1] or "financial_event_log" in parts[-1]:
            return "ledger"
        return parts[1]
    if parts and parts[0] == "scripts":
        return "scripts"
    return "other"


def physical_domain(relative_path: str) -> str:
    parts = normalize_path(relative_path).split("/")
    if len(parts) >= 2 and parts[0] == "smartcrypto":
        return parts[1]
    return parts[0] if parts else "other"


def domain_for_module(module: str) -> str:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "smartcrypto" and parts[1] in DOMAIN_ROOTS:
        if "ledger" in parts[-1] or "financial_event_log" in parts[-1]:
            return "ledger"
        return parts[1]
    return "other"


def policy_documented(project_root: Path) -> bool:
    path = project_root / POLICY_PATH
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig").lower()
    except OSError:
        return False
    markers = (
        "policy_status: active",
        "paper_only: true",
        "shadow_only: true",
        "live_trading_enabled: false",
        "order_submission_enabled: false",
        "real_order_submission_enabled: false",
        "exchange_private_access: false",
        "sends_orders: false",
        "changes_risk: false",
    )
    return all(marker in text for marker in markers)


def call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except (AttributeError, ValueError):
        return ""


def leaf_name(name: str) -> str:
    return name.rsplit(".", maxsplit=1)[-1]


def string_value(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, Path)):
        return str(node.value)
    if isinstance(node, ast.Name):
        return constants.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    if isinstance(node, ast.Call) and leaf_name(call_name(node)) == "Path" and node.args:
        return string_value(node.args[0], constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = string_value(node.left, constants)
        right = string_value(node.right, constants)
        if left and right:
            return normalize_path(f"{left}/{right}")
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return None


def open_mode(node: ast.Call) -> str:
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    name = leaf_name(call_name(node))
    position = 1 if name == "open" and isinstance(node.func, ast.Name) else 0
    if len(node.args) > position:
        candidate = node.args[position]
        if isinstance(candidate, ast.Constant):
            return str(candidate.value)
    return "r"


def function_context(parents: list[str]) -> str:
    return ".".join(parents) if parents else "<module>"


class BoundaryVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.constants: dict[str, str] = {}
        self.parents: list[str] = []
        self.imports: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.read_count = 0

    def visit_Assign(self, node: ast.Assign) -> None:
        value = string_value(node.value, self.constants)
        if value:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    self.constants[target.id] = value
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.parents.append(node.name)
        self.generic_visit(node)
        self.parents.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add_import(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._add_import(node.module, node.lineno)

    def _add_import(self, module: str, line: int) -> None:
        target_domain = domain_for_module(module)
        if target_domain != "other":
            self.imports.append({"module": module, "target_domain": target_domain, "line": line})

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        leaf = leaf_name(name)
        if leaf in READ_METHODS:
            self.read_count += 1
        operation: str | None = None
        target_node: ast.AST | None = None
        if leaf == "open":
            mode = open_mode(node)
            if any(marker in mode for marker in ("w", "a", "x", "+")):
                operation = f"open:{mode}"
                target_node = node.func.value if isinstance(node.func, ast.Attribute) else (node.args[0] if node.args else None)
            else:
                self.read_count += 1
        elif leaf in WRITE_METHODS:
            operation = leaf
            if isinstance(node.func, ast.Attribute):
                target_node = node.func.value
        elif leaf == "write" and isinstance(node.func, ast.Attribute):
            receiver = string_value(node.func.value, self.constants) or ""
            if receiver.lower() in {"file", "fh", "handle", "stream", "output_file"}:
                operation = leaf
                target_node = node.func.value
        elif leaf == "dump" and name in {"json.dump", "pickle.dump"} and len(node.args) >= 2:
            operation = leaf
            target_node = node.args[1]
        elif leaf == "commit" and isinstance(node.func, ast.Attribute):
            receiver = string_value(node.func.value, self.constants) or ""
            if any(marker in receiver.lower() for marker in ("connection", "conn", "database", "cursor")):
                operation = leaf
                target_node = node.func.value
        elif leaf == "replace" and isinstance(node.func, ast.Attribute):
            receiver = string_value(node.func.value, self.constants) or ""
            if any(marker in receiver.lower() for marker in ("path", "file")):
                operation = "atomic_replace"
                target_node = node.args[0] if node.args else node.func.value
        elif leaf in {"copy", "copy2", "move"} and len(node.args) >= 2:
            operation = leaf
            target_node = node.args[1]
        elif leaf in {"execute", "executemany", "executescript"} and node.args:
            statement = string_value(node.args[0], self.constants)
            if statement and statement.strip().lower().startswith(SQL_WRITE_PREFIXES):
                operation = f"sql:{statement.strip().split(maxsplit=1)[0].lower()}"
                target_node = node.func.value if isinstance(node.func, ast.Attribute) else None
        if operation:
            target = string_value(target_node, self.constants) or "unknown"
            self.writes.append(
                {
                    "line": node.lineno,
                    "function_or_class": function_context(self.parents),
                    "target_path_or_symbol": normalize_path(target),
                    "operation": operation,
                }
            )
        self.generic_visit(node)


def target_kind(relative_path: str, target: str) -> str:
    value = target.lower()
    module = relative_path.lower()
    if "report" in value or "evidence" in value or "snapshot" in value:
        return "report_or_evidence"
    if "ledger" in value or "financial_event_log" in value or "ledger" in module or "financial_event_log" in module:
        return "financial_ledger_or_event_log"
    if "state" in value or "runtime" in value or "kill_switch" in value:
        return "runtime_state"
    if target in {"handle", "connection", "self.path", "self.log_path", "sqlite_connection"}:
        return "symbolic_persistence_target"
    return "file_or_database"


def writer_classification(relative_path: str, domain: str, kind: str) -> tuple[str, str, str, str]:
    authority = KNOWN_AUTHORITIES.get(relative_path)
    if authority:
        return (
            "authorized_writer",
            "ok",
            authority,
            "Keep this writer behind its existing paper/shadow safety and public contract.",
        )
    if domain in {"ops", "scripts"} and kind == "report_or_evidence":
        return (
            "report_writer",
            "ok",
            "ops_reporting",
            "Keep output limited to reports, snapshots, or evidence artifacts.",
        )
    if domain == "dashboard":
        return (
            "ambiguous_dashboard_writer",
            "high",
            "none",
            "Move persistence behind an existing ops/state authority or keep the dashboard read-only.",
        )
    if domain == "risk":
        return (
            "ambiguous_risk_writer",
            "high",
            "none",
            "Risk code must delegate persistence to a named state or ledger authority.",
        )
    if kind in {"financial_ledger_or_event_log", "runtime_state"}:
        return (
            "ambiguous_state_or_ledger_writer",
            "high",
            "none",
            "Declare and document one existing authority; do not create a parallel writer.",
        )
    return (
        "unknown_writer_requires_review",
        "medium",
        "unresolved",
        "Confirm the target and delegate to an existing authority when it is runtime state.",
    )


def cross_import_classification(source_file: str, source: str, target: str) -> tuple[str, str, str]:
    if source == target:
        return "same_domain", "ok", "No boundary crossing."
    if source == "state" and target == "execution":
        return "improper_state_to_execution_dependency", "high", "State must not depend on execution behavior."
    if source == "dashboard" and target == "execution":
        return "improper_dashboard_to_execution_dependency", "high", "Dashboard must consume snapshots, not execution modules."
    if source == "dashboard" and target in {"state", "ledger", "risk"}:
        if source_file == "smartcrypto/dashboard/command_bus.py":
            return "documented_readonly_control_adapter", "medium", "Named fail-closed command audit adapter."
        return "dashboard_direct_domain_dependency", "medium", "Prefer an ops snapshot or read-only service adapter."
    allowed = {
        ("execution", "state"),
        ("execution", "ledger"),
        ("execution", "risk"),
        ("risk", "state"),
        ("risk", "ledger"),
        ("ops", "state"),
        ("ops", "ledger"),
        ("ops", "execution"),
        ("ops", "risk"),
        ("dashboard", "ops"),
        ("scripts", "state"),
        ("scripts", "ledger"),
        ("scripts", "execution"),
        ("scripts", "ops"),
        ("scripts", "risk"),
        ("scripts", "dashboard"),
    }
    if (source, target) in allowed:
        return "allowed_dependency", "ok", "Dependency follows the documented authority direction."
    return "cross_domain_requires_review", "medium", "Review and document this dependency direction."


def scoped_python_files(project_root: Path, files: list[str]) -> list[str]:
    scoped: list[str] = []
    for item in files:
        relative = normalize_path(item)
        if not relative.endswith(".py") or not (project_root / relative).is_file():
            continue
        if relative.startswith("scripts/") or any(relative.startswith(f"smartcrypto/{domain}/") for domain in DOMAIN_ROOTS):
            scoped.append(relative)
    return sorted(set(scoped))


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    files = scoped_python_files(root, list(discovery.files))
    modules: list[dict[str, Any]] = []
    writer_targets: list[dict[str, Any]] = []
    cross_domain_imports: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    for relative_path in files:
        try:
            source = (root / relative_path).read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=relative_path)
        except (OSError, UnicodeError, SyntaxError) as exc:
            parse_errors.append({"file": relative_path, "reason": type(exc).__name__})
            continue
        domain = domain_for_path(relative_path)
        visitor = BoundaryVisitor(relative_path)
        visitor.visit(tree)
        module_writers: list[dict[str, Any]] = []
        for raw in visitor.writes:
            kind = target_kind(relative_path, raw["target_path_or_symbol"])
            classification, severity, authority, recommendation = writer_classification(relative_path, domain, kind)
            item = {
                "file": relative_path,
                "line": raw["line"],
                "domain": domain,
                "function_or_class": raw["function_or_class"],
                "target_kind": kind,
                "target_path_or_symbol": raw["target_path_or_symbol"],
                "operation": raw["operation"],
                "classification": classification,
                "severity": severity,
                "authority": authority,
                "recommendation": recommendation,
            }
            writer_targets.append(item)
            module_writers.append(item)
            if severity != "ok":
                findings.append({**item, "finding_type": "writer_authority"})
        for imported in visitor.imports:
            target = imported["target_domain"]
            classification, severity, reason = cross_import_classification(relative_path, domain, target)
            item = {
                "file": relative_path,
                "line": imported["line"],
                "source_domain": domain,
                "target_domain": target,
                "module": imported["module"],
                "classification": classification,
                "severity": severity,
                "reason": reason,
            }
            cross_domain_imports.append(item)
            if severity != "ok":
                findings.append({**item, "finding_type": "cross_domain_import"})
        role = "writer" if module_writers else ("read_only_consumer" if visitor.read_count else "boundary_module")
        modules.append(
            {
                "file": relative_path,
                "domain": domain,
                "physical_domain": physical_domain(relative_path),
                "role": role,
                "authority": KNOWN_AUTHORITIES.get(relative_path),
                "writer_count": len(module_writers),
                "cross_domain_import_count": sum(
                    imported["target_domain"] != domain for imported in visitor.imports
                ),
            }
        )
    writer_targets.sort(key=lambda item: (item["file"], item["line"], item["operation"], item["target_path_or_symbol"]))
    cross_domain_imports.sort(key=lambda item: (item["file"], item["line"], item["module"]))
    findings.sort(key=lambda item: (item["file"], item["line"], item["finding_type"], item["classification"]))
    modules.sort(key=lambda item: item["file"])
    counts = {
        "modules": len(modules),
        "writers": len(writer_targets),
        "cross_domain_imports": len(cross_domain_imports),
        "findings": len(findings),
        "ok": sum(item["severity"] == "ok" for item in writer_targets + cross_domain_imports),
        "low": sum(item["severity"] == "low" for item in findings),
        "medium": sum(item["severity"] == "medium" for item in findings),
        "high": sum(item["severity"] == "high" for item in findings),
        "critical": sum(item["severity"] == "critical" for item in findings),
        "parse_errors": len(parse_errors),
    }
    documented = policy_documented(root)
    if parse_errors:
        status, reason = "blocked", "python_source_parse_failed"
    elif counts["critical"] or counts["high"]:
        status, reason = "blocked", "state_execution_ledger_boundary_violation"
    elif not documented:
        status, reason = "blocked", "boundary_policy_missing_or_incomplete"
    elif counts["medium"]:
        status, reason = "warning", "boundary_items_require_documented_review"
    else:
        status, reason = "ok", "state_execution_ledger_boundary_clean"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "scanned_files": len(files),
        "discovery_mode": discovery.mode,
        "discovery_source": discovery.source,
        "modules": modules,
        "boundary_findings": findings,
        "writer_targets": writer_targets,
        "cross_domain_imports": cross_domain_imports,
        "authority_map": AUTHORITY_MAP,
        "counts": counts,
        "critical_count": counts["critical"],
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "low_count": counts["low"],
        "policy_documented": documented,
        "parse_errors": parse_errors,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static state/execution/ledger authority boundary audit.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on", choices=("low", "medium", "high", "critical"), default="high")
    return parser.parse_args(argv)


def should_fail(report: dict[str, Any], threshold: str) -> bool:
    rank = SEVERITY_RANK[threshold]
    return any(SEVERITY_RANK.get(item.get("severity", "ok"), 0) >= rank for item in report["boundary_findings"])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"{report['status']}: {report['reason']} "
            f"({report['scanned_files']} files, {report['counts']['writers']} writers)"
        )
    return 1 if should_fail(report, args.fail_on) or report["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
