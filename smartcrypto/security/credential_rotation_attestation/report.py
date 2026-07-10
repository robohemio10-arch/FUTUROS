"""Safe report output for credential-rotation attestation."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

DEFAULT_REPORT_PATH = Path("data/reports/credential_rotation_attestation_gate_v1.json")
ALLOWED_REPORT_ROOT = Path("data/reports")


def resolve_report_path(root: Path, value: str | Path | None) -> Path:
    candidate = Path(value) if value is not None else DEFAULT_REPORT_PATH
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def validate_report_path(root: Path, path: Path, write_requested: bool) -> list[str]:
    if not write_requested:
        return []
    allowed = (root / ALLOWED_REPORT_ROOT).resolve()
    try:
        path.relative_to(allowed)
    except ValueError:
        return ["report_output_outside_data_reports"]
    if path.suffix.casefold() != ".json":
        return ["report_output_must_be_json"]
    return []


def write_safe_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
