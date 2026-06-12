from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
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
RUNTIME_PREFIXES = _DISCOVERY_MODULE.RUNTIME_PREFIXES
RUNTIME_SUFFIXES = _DISCOVERY_MODULE.RUNTIME_SUFFIXES
discover_versioned_files = _DISCOVERY_MODULE.discover_versioned_files
is_runtime_artifact = _DISCOVERY_MODULE.is_runtime_artifact


DEFAULT_OUTPUT = Path("PROJECT_MANIFEST_CLEAN.json")
GENERATED_BY = "scripts/generate_project_manifest.py"
SELF_PATH = "PROJECT_MANIFEST_CLEAN.json"
MANIFEST_VERSION = 3
TEXT_HASH_MODE = "text_lf"
BINARY_HASH_MODE = "binary_raw"


def is_utf8_text(content: bytes) -> bool:
    if b"\0" in content:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def normalize_text_line_endings(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def canonical_file_content(path: Path) -> tuple[bytes, str]:
    content = path.read_bytes()
    if is_utf8_text(content):
        return normalize_text_line_endings(content), TEXT_HASH_MODE
    return content, BINARY_HASH_MODE


def file_sha256(path: Path) -> str:
    canonical_content, _hash_mode = canonical_file_content(path)
    return hashlib.sha256(canonical_content).hexdigest()


def count_files(files: list[str]) -> dict[str, int]:
    return {
        "python_files": sum(path.endswith(".py") for path in files),
        "test_files": sum(path.startswith("tests/") and path.endswith(".py") for path in files),
        "docs_files": sum(path.startswith("docs/") or path.endswith(".md") for path in files),
        "dockerfiles": sum(path.endswith("Dockerfile") or "/Dockerfile" in path for path in files),
        "workflows": sum(path.startswith(".github/workflows/") for path in files),
    }


def aggregate_hash(manifest_files: list[dict[str, Any]], counts: dict[str, int]) -> str:
    payload = {
        "files": manifest_files,
        "counts": counts,
        "generated_by_script": GENERATED_BY,
        "manifest_version": MANIFEST_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_manifest(root: Path) -> dict[str, Any]:
    discovery = discover_versioned_files(root, manifest_path=SELF_PATH)
    tracked = discovery.files
    runtime_excluded = discovery.runtime_excluded_files
    included = [path for path in tracked if path != SELF_PATH and not is_runtime_artifact(path)]
    files = []
    for path in included:
        canonical_content, hash_mode = canonical_file_content(root / path)
        files.append(
            {
                "path": path,
                "bytes": len(canonical_content),
                "hash_mode": hash_mode,
                "sha256": hashlib.sha256(canonical_content).hexdigest(),
            }
        )
    counts = {
        "tracked_files_total": len(tracked),
        "manifested_files_total": len(files),
        "runtime_excluded_files_total": len(runtime_excluded),
        **count_files(included),
    }
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_by_script": GENERATED_BY,
        "hash_strategy": "sha256 over canonical text LF content or raw binary bytes",
        "byte_count_strategy": "canonical content bytes",
        "deterministic": True,
        "self_excluded_for_determinism": SELF_PATH,
        "runtime_artifacts_not_in_zip": True,
        "runtime_exclusions": {
            "prefixes": list(RUNTIME_PREFIXES),
            "suffixes": list(RUNTIME_SUFFIXES),
            "excluded_tracked_paths": runtime_excluded,
        },
        "counts": counts,
        "aggregate_sha256": aggregate_hash(files, counts),
        "files": files,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic clean project manifest.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve()
    output = root / args.output
    manifest = build_manifest(root)
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.exists():
            print(json.dumps({"status": "blocked", "reason": "manifest_missing"}, sort_keys=True))
            return 1
        current = output.read_text(encoding="utf-8")
        status = "ok" if current == rendered else "blocked"
        reason = "manifest_current" if status == "ok" else "manifest_outdated"
        print(json.dumps({"status": status, "reason": reason, "output": str(output)}, sort_keys=True))
        return 0 if status == "ok" else 1

    output.write_text(rendered, encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(output), "aggregate_sha256": manifest["aggregate_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
