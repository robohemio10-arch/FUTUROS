from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REVIEW_REGISTRY_PATH = "config/operational_exception_review_registry_v1.json"

SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "sends_orders": False,
    "changes_risk": False,
    "exchange_private_access": False,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
}
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}
LOG_METHODS = {"critical", "error", "exception", "warning"}
DIAGNOSTIC_NAMES = {"error", "errors", "failure", "failures", "reason", "status", "warning", "warnings"}
JUSTIFICATION_MARKERS = (
    "best effort",
    "defensive boundary",
    "defensive reporting",
    "intentional",
    "minimal test runtime",
    "optional",
    "skip invalid",
    "skip malformed",
    "status controlled",
)
CRITICAL_CONTEXT_MARKERS = (
    "order_submission",
    "order_manager",
    "risk_manager",
    "readiness",
    "live_release",
    "canary",
    "training_dataset",
    "trade_enriched",
)
HIGH_CONTEXT_MARKERS = (
    "audit_",
    "dashboard",
    "feedback_sync",
    "healthcheck",
    "manifest",
    "notification",
    "qlib",
    "runtime_evidence",
    "secret_scan",
    "scan_versioned_secrets",
)
FAIL_CLOSED_FUNCTION_PREFIXES = (
    "coerce",
    "extract",
    "git_",
    "inspect",
    "is_",
    "load",
    "parse",
    "read",
    "resolve",
    "safe",
    "try_",
    "validate",
)
KNOWN_BEST_EFFORT_CONTEXTS = (
    "build_model",
    "join_training_dataset",
    "ocr",
    "save_binance_recent",
    "train_",
    "write_decision",
)


def load_versioned_file_discovery() -> ModuleType:
    module_name = "smartcrypto.ops.versioned_file_discovery"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"smartcrypto", "smartcrypto.ops", module_name}:
            raise

    discovery_path = Path(__file__).resolve().parents[1] / "smartcrypto" / "ops" / "versioned_file_discovery.py"
    spec = importlib.util.spec_from_file_location(module_name, discovery_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot_load_standalone_module:{discovery_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
        raise
    return module


_DISCOVERY_MODULE = load_versioned_file_discovery()
discover_versioned_files = _DISCOVERY_MODULE.discover_versioned_files


def exception_name(node: ast.expr | None) -> set[str]:
    if node is None:
        return {"bare_except"}
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for item in node.elts:
            names.update(exception_name(item))
        return names
    return set()


def is_broad_handler(node: ast.ExceptHandler) -> bool:
    names = exception_name(node.type)
    return "bare_except" in names or bool(names & BROAD_EXCEPTION_NAMES)


def call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        parts = [function.attr]
        value = function.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def dict_string_values(node: ast.Dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=False):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values[key.value] = value.value
    return values


def dict_keys(node: ast.Dict) -> set[str]:
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def return_is_success(node: ast.Return) -> bool:
    if isinstance(node.value, ast.Dict):
        status = dict_string_values(node.value).get("status", "").lower()
        return status in {"ok", "success", "successful"}
    if isinstance(node.value, ast.Constant):
        return node.value.value is True
    return False


def return_is_controlled_failure(node: ast.Return) -> bool:
    if not isinstance(node.value, ast.Dict):
        return False
    values = dict_string_values(node.value)
    keys = dict_keys(node.value)
    status = values.get("status", "").lower()
    if {"status", "reason"} <= keys and status not in {"ok", "success", "successful"}:
        return True
    if "error" in keys and ({"available", "valid"} & keys):
        return True
    return status in {"blocked", "degraded", "error", "failed", "invalid", "warning"}


def return_is_controlled_call(node: ast.Return) -> bool:
    if not isinstance(node.value, ast.Call):
        return False
    name = call_name(node.value).lower().rsplit(".", maxsplit=1)[-1]
    if name in {"blocked", "failed", "generated_source"} or name.startswith(("blocked_", "failed_", "_blocked")):
        return True
    string_args = {
        argument.value.lower()
        for argument in node.value.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }
    return bool(string_args & {"blocked", "degraded", "error", "failed", "invalid", "warning"})


def return_is_fail_closed(node: ast.Return) -> bool:
    value = node.value
    if value is None:
        return True
    if isinstance(value, ast.Constant):
        return value.value in {None, False}
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        elements = getattr(value, "elts", None)
        return not value.keys if isinstance(value, ast.Dict) else not elements
    return False


def assigned_diagnostic_name(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and any(marker in item.id.lower() for marker in DIAGNOSTIC_NAMES):
            return True
        if isinstance(item, ast.Attribute) and any(marker in item.attr.lower() for marker in DIAGNOSTIC_NAMES):
            return True
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            if any(marker in item.value.lower() for marker in DIAGNOSTIC_NAMES):
                return True
    return False


def has_operational_diagnostic(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            name = call_name(node).lower()
            leaf = name.rsplit(".", maxsplit=1)[-1]
            if leaf in LOG_METHODS:
                return True
            if leaf in {"append", "update"} and assigned_diagnostic_name(node):
                return True
        if isinstance(node, ast.Return):
            if return_is_controlled_failure(node) or return_is_controlled_call(node):
                return True
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and assigned_diagnostic_name(node):
            return True
    return False


def handler_pattern(handler: ast.ExceptHandler) -> str | None:
    if has_operational_diagnostic(handler):
        return None
    returns = [node for node in ast.walk(handler) if isinstance(node, ast.Return)]
    if any(return_is_success(node) for node in returns):
        return "broad_exception_success_return"
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
        return "broad_exception_pass"
    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Continue):
        return "broad_exception_continue"
    if any(return_is_fail_closed(node) for node in returns):
        return "broad_exception_fail_closed_without_diagnostic"
    if returns:
        return "broad_exception_return_without_status"
    if any(isinstance(node, ast.Break) for node in ast.walk(handler)):
        return "broad_exception_break"
    return "broad_exception_fallback_without_diagnostic"


def context_is_fail_closed(function_name: str, pattern: str) -> bool:
    lowered = function_name.lower()
    function_context = lowered.rsplit(":", maxsplit=1)[-1]
    leaf = function_context.rsplit(".", maxsplit=1)[-1].lstrip("_")
    return (
        pattern == "broad_exception_fail_closed_without_diagnostic"
        or leaf.startswith(FAIL_CLOSED_FUNCTION_PREFIXES)
        or any(marker in lowered for marker in KNOWN_BEST_EFFORT_CONTEXTS)
    )


def classify_severity(relative_path: str, function_name: str, pattern: str) -> str:
    context = f"{relative_path}:{function_name}".lower()
    fail_closed = context_is_fail_closed(context, pattern)
    if any(marker in context for marker in CRITICAL_CONTEXT_MARKERS):
        return "medium" if fail_closed else "critical"
    if any(marker in context for marker in HIGH_CONTEXT_MARKERS):
        return "medium" if fail_closed else "high"
    if pattern in {"broad_exception_pass", "broad_exception_continue", "broad_exception_success_return"}:
        return "medium"
    if pattern == "broad_exception_fail_closed_without_diagnostic":
        return "low"
    return "medium"


def recommendation_for(pattern: str) -> str:
    if pattern == "broad_exception_success_return":
        return "Return a controlled non-success status with a sanitized reason, or re-raise the exception."
    if pattern == "broad_exception_continue":
        return "Record a sanitized per-item error before continuing, preserving the batch status."
    if pattern == "broad_exception_fail_closed_without_diagnostic":
        return "Keep the fail-closed fallback but expose a sanitized status, reason, or warning."
    return "Add a sanitized diagnostic or controlled failure status; re-raise when the caller owns recovery."


def reason_for(pattern: str) -> str:
    reasons = {
        "broad_exception_pass": "Broad exception is silently discarded.",
        "broad_exception_continue": "Broad exception skips work without a diagnostic.",
        "broad_exception_success_return": "Broad exception can be reported as success.",
        "broad_exception_fail_closed_without_diagnostic": "Fail-closed fallback hides its failure reason.",
        "broad_exception_return_without_status": "Broad exception returns a fallback without controlled status.",
        "broad_exception_break": "Broad exception stops iteration without a diagnostic.",
        "broad_exception_fallback_without_diagnostic": "Broad exception uses an undocumented fallback without a diagnostic.",
    }
    return reasons[pattern]


class ExceptionSwallowingVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str, source_lines: list[str]) -> None:
        self.relative_path = relative_path
        self.source_lines = source_lines
        self.context_stack: list[str] = []
        self.class_kind_stack: list[str | None] = []
        self.findings: list[dict[str, Any]] = []
        self.ignored_false_positive_count = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_names = {name for base in node.bases for name in exception_name(base)}
        class_kind: str | None = None
        if base_names & BROAD_EXCEPTION_NAMES or any(name.endswith(("Error", "Exception")) for name in base_names):
            class_kind = "custom_exception"
        elif base_names & {"ABC", "Protocol"}:
            class_kind = "abstract_contract"
        if class_kind and any(isinstance(item, ast.Pass) for item in node.body):
            self.ignored_false_positive_count += 1
        self.context_stack.append(node.name)
        self.class_kind_stack.append(class_kind)
        self.generic_visit(node)
        self.class_kind_stack.pop()
        self.context_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorators = {call_name(item) if isinstance(item, ast.Call) else getattr(item, "id", getattr(item, "attr", "")) for item in node.decorator_list}
        if self.class_kind_stack and self.class_kind_stack[-1] == "abstract_contract":
            if any(isinstance(item, ast.Pass) for item in node.body) or "abstractmethod" in decorators:
                self.ignored_false_positive_count += 1
        self.context_stack.append(node.name)
        self.generic_visit(node)
        self.context_stack.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if not is_broad_handler(node):
            self.generic_visit(node)
            return
        if self.relative_path.startswith("tests/"):
            self.ignored_false_positive_count += 1
            return
        pattern = handler_pattern(node)
        if pattern is None:
            self.generic_visit(node)
            return
        if self._has_explicit_justification(node):
            self.ignored_false_positive_count += 1
            return
        context = ".".join(self.context_stack) or "<module>"
        severity = classify_severity(self.relative_path, context, pattern)
        self.findings.append(
            {
                "severity": severity,
                "file": self.relative_path,
                "line": node.lineno,
                "function_or_class": context,
                "pattern": pattern,
                "reason": reason_for(pattern),
                "recommendation": recommendation_for(pattern),
            }
        )
        self.generic_visit(node)

    def _has_explicit_justification(self, node: ast.ExceptHandler) -> bool:
        start = max(0, node.lineno - 2)
        end = min(len(self.source_lines), getattr(node, "end_lineno", node.lineno) + 1)
        source = "\n".join(self.source_lines[start:end]).lower()
        return any(marker in source for marker in JUSTIFICATION_MARKERS)


def audit_python_file(path: Path, relative_path: str) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=relative_path)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        error = {
            "severity": "high",
            "file": relative_path,
            "line": getattr(exc, "lineno", 0) or 0,
            "function_or_class": "<module>",
            "pattern": "python_source_not_auditable",
            "reason": f"Static audit could not parse the file: {type(exc).__name__}.",
            "recommendation": "Fix the source or encoding so the operational audit can inspect it.",
        }
        return [error], 0, error
    visitor = ExceptionSwallowingVisitor(relative_path, source.splitlines())
    visitor.visit(tree)
    return visitor.findings, visitor.ignored_false_positive_count, None


def _load_review_registry(project_root: Path) -> dict[str, Any]:
    path = project_root / REVIEW_REGISTRY_PATH
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != "operational_exception_review_registry_v1":
        raise ValueError("unexpected_operational_exception_review_registry_schema")
    return payload


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finding_set_sha256(findings: list[dict[str, Any]], root: Path) -> str:
    rows = []
    cache: dict[str, str] = {}
    for item in findings:
        file_name = item["file"]
        source_hash = cache.setdefault(file_name, _source_sha256(root / file_name))
        rows.append({key: item.get(key) for key in ("file", "line", "function_or_class", "pattern", "severity")} | {"source_sha256": source_hash})
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    python_files = sorted(path for path in discovery.files if path.endswith(".py") and (root / path).is_file())
    findings: list[dict[str, Any]] = []
    ignored_count = 0
    parse_error_count = 0
    for relative_path in python_files:
        file_findings, file_ignored, parse_error = audit_python_file(root / relative_path, relative_path)
        findings.extend(file_findings)
        ignored_count += file_ignored
        parse_error_count += int(parse_error is not None)

    findings.sort(key=lambda item: (-SEVERITY_RANK[item["severity"]], item["file"], item["line"], item["pattern"]))
    review_registry = _load_review_registry(root)
    reviewed_findings: list[dict[str, Any]] = []
    if findings and not any(item.get("severity") in {"high", "critical"} for item in findings):
        current_digest = _finding_set_sha256(findings, root)
        if (
            review_registry.get("reviewed_finding_count") == len(findings)
            and review_registry.get("reviewed_finding_set_sha256") == current_digest
        ):
            reviewed_findings = [dict(item) for item in findings]
            findings = []
    counts = {severity: sum(item["severity"] == severity for item in findings) for severity in SEVERITY_RANK}
    if counts["critical"] or counts["high"]:
        status = "blocked"
        reason = "high_or_critical_exception_swallowing_detected"
    elif findings:
        status = "warning"
        reason = "non_critical_exception_swallowing_findings_detected"
    else:
        status = "ok"
        reason = "no_unhandled_exception_swallowing_detected"
    return {
        "status": status,
        "reason": reason,
        "scanned_files": len(python_files),
        "file_discovery_mode": discovery.mode,
        "file_discovery_source": discovery.source,
        "findings": findings,
        "finding_count": len(findings),
        "reviewed_findings": reviewed_findings,
        "reviewed_finding_count": len(reviewed_findings),
        "review_registry_path": REVIEW_REGISTRY_PATH,
        "critical_count": counts["critical"],
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "low_count": counts["low"],
        "parse_error_count": parse_error_count,
        "ignored_false_positive_count": ignored_count,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit broad exception handlers without importing audited modules.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on", choices=("critical", "high", "medium", "low", "none"), default="high")
    return parser.parse_args(argv)


def exit_code_for(report: dict[str, Any], fail_on: str) -> int:
    if fail_on == "none":
        return 0
    threshold = SEVERITY_RANK[fail_on]
    return int(any(SEVERITY_RANK[item["severity"]] >= threshold for item in report["findings"]))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"{report['status']}: {report['reason']} "
            f"({report['finding_count']} findings, {report['scanned_files']} files scanned)"
        )
    return exit_code_for(report, args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
