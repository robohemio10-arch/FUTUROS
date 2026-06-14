from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


SCHEMA_VERSION = "bitradex_dependency_boundary_cleanup_v1"
POLICY_PATH = "docs/BITRADEX_DEPENDENCY_BOUNDARY_CLEANUP_V1.md"
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
    "runs_ocr": False,
    "imports_trades": False,
    "writes_trades_master": False,
    "writes_official_trades_master": False,
    "changes_training_dataset": False,
    "changes_model": False,
}
OFFICIAL_MASTER_WRITERS = {
    "scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py",
    "scripts/import_trades_incremental.py",
    "scripts/large_trades_import_quality_gate.py",
}
OFFICIAL_DATASET_BUILDERS = {
    "scripts/rebuild_phase5_datasets.py",
    "scripts/build_training_dataset.py",
    "scripts/build_quality_gated_shadow_compatible_dataset_v1.py",
}
OFFICIAL_OCR_STAGING_WRITERS = {
    "scripts/ocr_bitradex_images_to_review.py",
    "scripts/repair_price_scale_ocr_anomalies.py",
}
FORBIDDEN_IMPORT_PREFIXES = (
    "smartcrypto.execution",
    "smartcrypto.risk",
    "smartcrypto.qlib_engine",
)
MODEL_IMPORT_PREFIXES = ("smartcrypto.ml.model", "smartcrypto.ml.train", "smartcrypto.ml.registry")
TRADING_RUNTIME_PREFIXES = ("smartcrypto/execution/", "smartcrypto/risk/", "freqtrade/")
WRITE_METHODS = {"to_csv", "to_excel", "to_json", "to_parquet", "write_bytes", "write_text"}
OFFICIAL_TARGETS = {
    "trades_master": ("trades_master.xlsx", "trades_master.parquet"),
    "training_dataset": (
        "training_dataset.parquet",
        "training_dataset_quality_gated",
        "trade_enriched.parquet",
    ),
    "model": ("data/models/", "models/registry/"),
    "active_signals": ("active_freqtrade_signals.json", "active_signals"),
    "risk_or_readiness": ("risk_manager", "readiness", "canary", "live_release"),
}
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


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


def is_bitradex_related(relative_path: str, source: str = "") -> bool:
    normalized = normalize_path(relative_path).lower()
    name = normalized.rsplit("/", maxsplit=1)[-1]
    return (
        normalized.startswith("bitradex_realtime_candle_collector_v1/")
        or "bitradex" in name
        or "ocr" in name
        or "bitradex" in source.lower()
        or "ocr" in source.lower()
    )


def source_role(relative_path: str) -> str:
    path = normalize_path(relative_path).lower()
    name = path.rsplit("/", maxsplit=1)[-1]
    if path.startswith("bitradex_realtime_candle_collector_v1/") or path.startswith(
        "bitradex_realtime_collector_"
    ):
        return "bitradex_collector"
    if path.startswith("smartcrypto/dashboard/"):
        return "dashboard_read_only_consumer"
    if path.startswith(TRADING_RUNTIME_PREFIXES):
        return "trading_runtime"
    if path.startswith("scripts/") and ("bitradex" in name or "ocr" in name):
        if "audit" in name or "diagnose" in name or "inspect" in name:
            return "offline_audit_reader"
        if "apply" in name or "import" in name:
            return "controlled_import"
        return "ocr_or_staging"
    if path.startswith("smartcrypto/ops/"):
        return "ops_read_only_or_report"
    return "related_consumer"


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
        "runs_ocr: false",
        "imports_trades: false",
    )
    return all(marker in text for marker in markers)


def call_name(node: ast.Call) -> str:
    try:
        return ast.unparse(node.func)
    except (AttributeError, ValueError):
        return ""


def leaf_name(value: str) -> str:
    return value.rsplit(".", maxsplit=1)[-1]


def expression_text(node: ast.AST | None, constants: dict[str, str]) -> str:
    if node is None:
        return "unknown"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, node.id)
    if isinstance(node, ast.Call) and leaf_name(call_name(node)) == "Path" and node.args:
        return expression_text(node.args[0], constants)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return normalize_path(f"{expression_text(node.left, constants)}/{expression_text(node.right, constants)}")
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return "unknown"


def open_mode(node: ast.Call) -> str:
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    position = 1 if isinstance(node.func, ast.Name) else 0
    if len(node.args) > position:
        candidate = node.args[position]
        if isinstance(candidate, ast.Constant):
            return str(candidate.value)
    return "r"


class BoundaryVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.constants: dict[str, str] = {}
        self.context: list[str] = []
        self.imports: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        value = expression_text(node.value, self.constants)
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper() and value != "unknown":
                self.constants[target.id] = value
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.context.append(node.name)
        self.generic_visit(node)
        self.context.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append({"module": alias.name, "line": node.lineno, "function_or_class": self._context()})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append({"module": node.module, "line": node.lineno, "function_or_class": self._context()})

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        leaf = leaf_name(name)
        operation: str | None = None
        target: ast.AST | None = None
        if leaf == "open":
            mode = open_mode(node)
            if any(marker in mode for marker in ("w", "a", "x", "+")):
                operation = f"open:{mode}"
                target = node.func.value if isinstance(node.func, ast.Attribute) else (node.args[0] if node.args else None)
        elif leaf in WRITE_METHODS and isinstance(node.func, ast.Attribute):
            operation = leaf
            target = node.func.value
        elif leaf == "dump" and name in {"json.dump", "pickle.dump"} and len(node.args) >= 2:
            operation = leaf
            target = node.args[1]
        elif leaf in {"copy", "copy2", "move"} and len(node.args) >= 2:
            operation = leaf
            target = node.args[1]
        elif name == "sqlite3.connect" and node.args:
            operation = "sqlite_connect"
            target = node.args[0]
        if operation:
            self.writes.append(
                {
                    "line": node.lineno,
                    "function_or_class": self._context(),
                    "operation": operation,
                    "target": normalize_path(expression_text(target, self.constants)),
                }
            )
        self.generic_visit(node)

    def _context(self) -> str:
        return ".".join(self.context) if self.context else "<module>"


def finding(
    severity: str,
    file: str,
    line: int,
    function_or_class: str,
    pattern: str,
    reason: str,
    recommendation: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "file": file,
        "line": line,
        "function_or_class": function_or_class,
        "pattern": pattern,
        "reason": reason,
        "recommendation": recommendation,
    }


def classify_import(relative_path: str, role: str, item: dict[str, Any]) -> dict[str, Any] | None:
    module = str(item["module"])
    base = (relative_path, int(item["line"]), str(item["function_or_class"]))
    if role in {"bitradex_collector", "ocr_or_staging", "controlled_import"}:
        if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
            return finding(
                "critical",
                *base,
                "bitradex_or_ocr_imports_trading_runtime",
                f"Bitradex/OCR boundary imports prohibited runtime module {module}.",
                "Remove the dependency and keep ingestion independent from execution, risk, and Qlib runtime.",
            )
        if module.startswith(MODEL_IMPORT_PREFIXES):
            return finding(
                "high",
                *base,
                "ocr_or_staging_imports_model_runtime",
                f"OCR/staging imports model runtime module {module}.",
                "Keep model training and promotion outside the OCR/staging boundary.",
            )
        if module == "smartcrypto.ml.price_scale_ocr_repair":
            return finding(
                "medium",
                *base,
                "offline_ocr_repair_dependency",
                "Offline OCR repair reuses an ML namespace utility, creating a naming boundary ambiguity.",
                "Keep the call offline and document that it cannot train or promote a model.",
            )
    if role == "trading_runtime" and ("bitradex" in module.lower() or "ocr" in module.lower()):
        return finding(
            "high",
            *base,
            "trading_runtime_imports_bitradex_or_ocr",
            f"Trading runtime imports ingestion module {module} without a read-only snapshot contract.",
            "Consume a validated feature/snapshot contract instead of the collector or OCR implementation.",
        )
    if role == "dashboard_read_only_consumer" and ("staging" in module.lower() or "import_trades" in module.lower()):
        return finding(
            "high",
            *base,
            "dashboard_imports_staging_or_importer",
            f"Dashboard imports mutable staging/import module {module}.",
            "Use an ops snapshot or controlled read-only loader.",
        )
    return None


def target_category(target: str) -> str:
    lowered = target.lower()
    for category, markers in OFFICIAL_TARGETS.items():
        if any(marker in lowered for marker in markers):
            return category
    if "data/reports" in lowered or "report" in lowered or "summary" in lowered:
        return "report"
    if "staging" in lowered or "ocr" in lowered or "data/output" in lowered or "data/raw" in lowered:
        return "staging"
    if lowered.endswith((".sqlite", ".sqlite3", ".db")):
        return "sqlite"
    return "unknown"


def classify_write(relative_path: str, role: str, item: dict[str, Any]) -> dict[str, Any] | None:
    target = str(item["target"])
    category = target_category(target)
    base = (relative_path, int(item["line"]), str(item["function_or_class"]))
    if role == "dashboard_read_only_consumer":
        return finding(
            "high",
            *base,
            "dashboard_writes_bitradex_boundary",
            f"Dashboard performs {item['operation']} against {target}.",
            "Keep dashboard consumption read-only and move persistence to an existing ops authority.",
        )
    if category == "trades_master":
        if relative_path in OFFICIAL_MASTER_WRITERS:
            return None
        return finding(
            "critical",
            *base,
            "unauthorized_trades_master_writer",
            f"Non-authorized Bitradex/OCR path writes the official trades master target {target}.",
            "Delegate to an official import script with preview, backup, deduplication, and audit gates.",
        )
    if category == "training_dataset":
        if relative_path in OFFICIAL_DATASET_BUILDERS:
            return None
        return finding(
            "critical",
            *base,
            "unauthorized_training_dataset_writer",
            f"Non-authorized Bitradex/OCR path writes official dataset target {target}.",
            "Use the official Phase 5 rebuild and quality-gate sequence.",
        )
    if category in {"model", "active_signals", "risk_or_readiness"}:
        return finding(
            "critical" if category == "model" else "high",
            *base,
            f"ocr_boundary_writes_{category}",
            f"Bitradex/OCR boundary writes prohibited {category} target {target}.",
            "Remove the writer; ingestion cannot change models, signals, risk, readiness, canary, or live state.",
        )
    if category == "sqlite" and role != "bitradex_collector":
        return finding(
            "medium",
            *base,
            "sqlite_writer_requires_authority_review",
            f"SQLite target {target} is written outside the named collector storage boundary.",
            "Document the audit/staging database authority and confirm it is not an operational trading DB.",
        )
    if category == "report" and role not in {"offline_audit_reader", "ops_read_only_or_report"}:
        return finding(
            "medium",
            *base,
            "report_writer_policy_review",
            f"Writer targets report-like output {target} from role {role}.",
            "Keep the report runtime-only and document its producing authority.",
        )
    if category == "staging" and role in {"bitradex_collector", "ocr_or_staging", "controlled_import"}:
        return None
    return None


def requirement_findings(project_root: Path, files: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for relative_path in files:
        normalized = normalize_path(relative_path)
        if not normalized.startswith("bitradex_realtime_candle_collector_v1/") or not normalized.endswith("requirements.txt"):
            continue
        try:
            lines = (project_root / normalized).read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        seen: set[str] = set()
        for line_number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().lower()
            if name in seen:
                results.append(
                    finding(
                        "medium",
                        normalized,
                        line_number,
                        "<requirements>",
                        "duplicate_collector_dependency",
                        f"Collector dependency {name} is declared more than once.",
                        "Keep one reviewed declaration in the collector requirements policy.",
                    )
                )
            seen.add(name)
            if "==" not in line or "--hash=sha256:" not in line:
                results.append(
                    finding(
                        "medium",
                        normalized,
                        line_number,
                        "<requirements>",
                        "collector_dependency_not_hash_locked",
                        f"Collector dependency declaration is not an exact hash lock: {line}.",
                        "Track exact hashes in the dedicated lockfile-hardening follow-up; do not invent hashes here.",
                    )
                )
    return results


def audit_project(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    discovery = discover_versioned_files(root)
    versioned = sorted(normalize_path(item) for item in discovery.files)
    python_files = [item for item in versioned if item.endswith(".py") and (root / item).is_file()]
    source_cache: dict[str, str] = {}
    for relative_path in python_files:
        try:
            source_cache[relative_path] = (root / relative_path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
    scoped = sorted(
        path
        for path, source in source_cache.items()
        if not path.startswith("tests/")
        and (
            is_bitradex_related(path, source)
            or path.startswith(TRADING_RUNTIME_PREFIXES)
            or path.startswith("smartcrypto/dashboard/")
        )
    )
    bitradex_files: list[dict[str, Any]] = []
    dependency_findings: list[dict[str, Any]] = []
    write_findings: list[dict[str, Any]] = []
    cross_boundary_imports: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    for relative_path in scoped:
        source = source_cache[relative_path]
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            parse_errors.append({"file": relative_path, "line": exc.lineno or 0, "reason": "syntax_error"})
            continue
        role = source_role(relative_path)
        visitor = BoundaryVisitor()
        visitor.visit(tree)
        relevant = is_bitradex_related(relative_path, source)
        if relevant:
            bitradex_files.append(
                {
                    "file": relative_path,
                    "role": role,
                    "imports": len(visitor.imports),
                    "writes": len(visitor.writes),
                }
            )
        for imported in visitor.imports:
            classified = classify_import(relative_path, role, imported)
            if classified:
                dependency_findings.append(classified)
                cross_boundary_imports.append(
                    {
                        **classified,
                        "source_role": role,
                        "imported_module": imported["module"],
                    }
                )
        if relevant:
            for write in visitor.writes:
                classified = classify_write(relative_path, role, write)
                if classified:
                    write_findings.append({**classified, "operation": write["operation"], "target": write["target"]})
    dependency_findings.extend(requirement_findings(root, versioned))
    for collection in (bitradex_files, dependency_findings, write_findings, cross_boundary_imports, parse_errors):
        collection.sort(key=lambda item: (str(item.get("file", "")), int(item.get("line", 0)), str(item.get("pattern", ""))))
    all_findings = sorted(
        dependency_findings + write_findings,
        key=lambda item: (item["file"], item["line"], item["pattern"], item["severity"]),
    )
    counts = {
        level: sum(item["severity"] == level for item in all_findings)
        for level in ("critical", "high", "medium", "low")
    }
    documented = policy_documented(root)
    if parse_errors:
        status, reason = "blocked", "bitradex_python_source_parse_failed"
    elif counts["critical"] or counts["high"]:
        status, reason = "blocked", "bitradex_dependency_boundary_violation"
    elif not documented:
        status, reason = "blocked", "bitradex_dependency_policy_missing_or_incomplete"
    elif counts["medium"] or counts["low"]:
        status, reason = "warning", "bitradex_boundary_backlog_documented"
    else:
        status, reason = "ok", "bitradex_dependency_boundary_clean"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "scanned_files": len(scoped),
        "file_discovery_mode": discovery.mode,
        "file_discovery_source": discovery.source,
        "bitradex_files": bitradex_files,
        "dependency_findings": dependency_findings,
        "write_findings": write_findings,
        "cross_boundary_imports": cross_boundary_imports,
        "critical_count": counts["critical"],
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "low_count": counts["low"],
        "finding_count": len(all_findings),
        "policy_documented": documented,
        "parse_errors": parse_errors,
        **SAFETY_FLAGS,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static Bitradex/OCR dependency boundary audit.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_project(Path(args.project_root))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']} ({report['finding_count']} findings)")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
