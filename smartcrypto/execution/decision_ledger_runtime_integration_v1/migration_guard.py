"""Static guard for the legacy strategy decision writer migration."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Literal


class LegacyWriterGuardError(RuntimeError):
    pass


def inspect_legacy_strategy_writer(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(source_path))

    method_found = False
    broad_continue_handlers: list[int] = []
    append_calls: list[int] = []
    decision_log_path_mentions = text.count("freqtrade_signal_decisions.jsonl")

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_write_decision":
            method_found = True
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    is_broad = child.type is None or (
                        isinstance(child.type, ast.Name)
                        and child.type.id in {"Exception", "BaseException"}
                    )
                    only_continue = bool(child.body) and all(
                        isinstance(statement, ast.Continue)
                        for statement in child.body
                    )
                    if is_broad and only_continue:
                        broad_continue_handlers.append(child.lineno)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "write":
                        append_calls.append(child.lineno)

    return {
        "schema_version": "legacy_strategy_writer_guard_v1",
        "path": str(source_path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "legacy_writer_method_found": method_found,
        "broad_continue_handler_count": len(broad_continue_handlers),
        "broad_continue_handler_lines": broad_continue_handlers,
        "append_call_count": len(append_calls),
        "append_call_lines": append_calls,
        "decision_log_path_mention_count": decision_log_path_mentions,
        "canonical_writer": False,
        "retirement_allowed": False,
        "runtime_integration_allowed": False,
    }


def validate_migration_mode(
    *,
    mode: Literal["legacy_only", "shadow_compare", "canonical_only"],
    report: dict[str, Any],
) -> dict[str, Any]:
    if mode == "canonical_only":
        raise LegacyWriterGuardError("canonical_only_blocked_in_p0_4c")
    if not report.get("legacy_writer_method_found"):
        raise LegacyWriterGuardError("legacy_writer_missing_unexpectedly")
    return {
        "status": "pass",
        "mode": mode,
        "legacy_writer_retained": True,
        "canonical_writer_enabled": False,
        "dual_write_enabled": False,
        "runtime_integration_allowed": False,
    }
