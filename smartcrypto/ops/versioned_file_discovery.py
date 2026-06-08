from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUNTIME_PREFIXES = (
    "data/",
    "evidence/",
    "logs/",
    "models/",
    "reports/",
    "freqtrade/user_data/logs/",
    "freqtrade/user_data/data/",
    "bitradex_realtime_candle_collector_v1/data/",
    "bitradex_realtime_candle_collector_v1/logs/",
)
RUNTIME_SUFFIXES = (
    ".parquet",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".csv",
    ".xlsx",
    ".jsonl",
    ".zip",
    ".log",
)
STANDALONE_DENY_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "venv",
}
STANDALONE_TEXT_SUFFIXES = {
    ".cfg",
    ".dockerignore",
    ".ini",
    ".json",
    ".lock",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
STANDALONE_ALLOWED_NAMES = {
    ".env.example",
    ".gitignore",
    "constraints.txt",
    "Dockerfile",
    "Makefile",
    "PROJECT_MANIFEST_CLEAN.json",
    "requirements-dev.lock",
    "requirements-runtime.lock",
}


@dataclass(frozen=True)
class VersionedFileDiscovery:
    files: list[str]
    runtime_excluded_files: list[str]
    mode: str
    source: str


def normalize_relative_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


def is_runtime_artifact(path: str) -> bool:
    normalized = normalize_relative_path(path)
    return normalized.startswith(RUNTIME_PREFIXES) or normalized.lower().endswith(RUNTIME_SUFFIXES)


def is_forbidden_standalone_path(path: str) -> bool:
    normalized = normalize_relative_path(path)
    normalized_as_dir = normalized + "/" if normalized else normalized
    parts = normalized.split("/")
    name = parts[-1] if parts else normalized
    lower_name = name.lower()
    if any(part in STANDALONE_DENY_DIR_NAMES for part in parts):
        return True
    if normalized_as_dir in RUNTIME_PREFIXES or any(prefix.startswith(normalized_as_dir) for prefix in RUNTIME_PREFIXES):
        return True
    if normalized == ".env" or (lower_name.startswith(".env.") and lower_name != ".env.example"):
        return True
    if name.startswith("~$") or lower_name.endswith((".pyc", ".pyo", ".pyd", ".pem", ".key", ".crt", ".tmp")):
        return True
    return is_runtime_artifact(normalized)


def is_standalone_versionable_file(path: str) -> bool:
    normalized = normalize_relative_path(path)
    name = normalized.rsplit("/", maxsplit=1)[-1]
    if is_forbidden_standalone_path(normalized):
        return False
    if name in STANDALONE_ALLOWED_NAMES or name.endswith("Dockerfile"):
        return True
    return Path(name).suffix in STANDALONE_TEXT_SUFFIXES


def git_tracked_files(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git ls-files failed")
    return sorted(normalize_relative_path(path) for path in completed.stdout.splitlines() if path.strip())


def try_git_tracked_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        return git_tracked_files(root)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None


def baseline_manifest_files(root: Path, manifest_path: str = "PROJECT_MANIFEST_CLEAN.json") -> list[str] | None:
    path = root / manifest_path
    if not path.exists():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    files = [normalize_relative_path(item["path"]) for item in payload.get("files", []) if isinstance(item, dict) and item.get("path")]
    excluded = [
        normalize_relative_path(item)
        for item in payload.get("runtime_exclusions", {}).get("excluded_tracked_paths", [])
        if isinstance(item, str) and item
    ]
    return sorted({*files, *excluded, normalize_relative_path(manifest_path)})


def filesystem_versionable_files(root: Path, manifest_path: str = "PROJECT_MANIFEST_CLEAN.json") -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        relative_dir = "" if current == root else normalize_relative_path(current.relative_to(root))
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            child = current / dirname
            child_relative = normalize_relative_path(Path(relative_dir) / dirname) if relative_dir else dirname
            if child.is_symlink() or is_forbidden_standalone_path(child_relative + "/"):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink():
                continue
            relative = normalize_relative_path(path.relative_to(root))
            if is_standalone_versionable_file(relative):
                files.append(relative)
    files.append(normalize_relative_path(manifest_path))
    return sorted(set(files))


def discover_versioned_files(root: Path, manifest_path: str = "PROJECT_MANIFEST_CLEAN.json") -> VersionedFileDiscovery:
    resolved_root = root.resolve()
    tracked = try_git_tracked_files(resolved_root)
    if tracked is not None:
        return VersionedFileDiscovery(
            files=tracked,
            runtime_excluded_files=sorted(path for path in tracked if is_runtime_artifact(path)),
            mode="git",
            source="git ls-files",
        )

    baseline = baseline_manifest_files(resolved_root, manifest_path=manifest_path)
    if baseline is not None:
        return VersionedFileDiscovery(
            files=baseline,
            runtime_excluded_files=sorted(path for path in baseline if is_runtime_artifact(path)),
            mode="manifest_baseline",
            source=manifest_path,
        )

    walked = filesystem_versionable_files(resolved_root, manifest_path=manifest_path)
    return VersionedFileDiscovery(
        files=walked,
        runtime_excluded_files=sorted(path for path in walked if is_runtime_artifact(path)),
        mode="filesystem",
        source="standalone filesystem walk",
    )
