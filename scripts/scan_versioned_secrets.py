from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def load_versioned_file_discovery() -> ModuleType:
    module_name = "smartcrypto.ops.versioned_file_discovery"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name not in {"smartcrypto", "smartcrypto.ops", module_name}:
            raise

    _DISCOVERY_PATH = Path(__file__).resolve().parents[1] / "smartcrypto" / "ops" / "versioned_file_discovery.py"
    _DISCOVERY_SPEC = importlib.util.spec_from_file_location(module_name, _DISCOVERY_PATH)
    if _DISCOVERY_SPEC is None or _DISCOVERY_SPEC.loader is None:
        raise ModuleNotFoundError(f"cannot_load_standalone_module:{_DISCOVERY_PATH}")
    _DISCOVERY_MODULE = importlib.util.module_from_spec(_DISCOVERY_SPEC)
    sys.modules[module_name] = _DISCOVERY_MODULE
    try:
        _DISCOVERY_SPEC.loader.exec_module(_DISCOVERY_MODULE)
    except Exception:
        if sys.modules.get(module_name) is _DISCOVERY_MODULE:
            del sys.modules[module_name]
        raise
    return _DISCOVERY_MODULE


_DISCOVERY_MODULE = load_versioned_file_discovery()
discover_versioned_files = _DISCOVERY_MODULE.discover_versioned_files
is_runtime_artifact = _DISCOVERY_MODULE.is_runtime_artifact

SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".parquet",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".csv",
    ".xlsx",
    ".jsonl",
    ".log",
)
SECRET_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "generic_secret_assignment": re.compile(
        r"(?i)\b(api[_-]?key|secret|token|private[_-]?key)\b\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{24,}['\"]"
    ),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def should_scan(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if is_runtime_artifact(normalized):
        return False
    return not normalized.lower().endswith(SKIP_SUFFIXES)


def scan_file(path: Path, relative_path: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in SECRET_PATTERNS.items():
            if pattern.search(line):
                findings.append(
                    {
                        "path": relative_path,
                        "line": line_number,
                        "pattern": pattern_name,
                    }
                )
    return findings


def run_secret_scan(root: Path) -> dict[str, Any]:
    discovery = discover_versioned_files(root)
    tracked = discovery.files
    scanned = [path for path in tracked if should_scan(path)]
    findings: list[dict[str, Any]] = []
    for relative_path in scanned:
        findings.extend(scan_file(root / relative_path, relative_path))

    status = "blocked" if findings else "ok"
    return {
        "status": status,
        "reason": "secret_findings_detected" if findings else "no_versioned_secrets_detected",
        "scanned_files": len(scanned),
        "skipped_runtime_or_binary_files": len(tracked) - len(scanned),
        "file_discovery_mode": discovery.mode,
        "file_discovery_source": discovery.source,
        "findings": findings,
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan versioned source files for secret-like values.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_secret_scan(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{report['status']}: {report['reason']} ({report['scanned_files']} files scanned)")
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
