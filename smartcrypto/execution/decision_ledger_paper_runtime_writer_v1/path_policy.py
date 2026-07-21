"""Read-only path policy for a future paper decision-ledger writer."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .contracts import (
    CANONICAL_ALLOWED_ROOT,
    PaperRuntimeWriterProfileV1,
    PathPolicyReportV1,
)


def evaluate_path_policy(
    *, project_root: Path, profile: PaperRuntimeWriterProfileV1
) -> PathPolicyReportV1:
    """Resolve configured paths without creating directories or files."""

    root = project_root.expanduser().resolve(strict=False)
    relative_values = (profile.allowed_root, profile.ledger_path, profile.health_path)
    relative_paths_valid = all(_is_safe_posix_relative_path(value) for value in relative_values)

    allowed_root_lexical = _join_relative(root, profile.allowed_root)
    ledger_path_lexical = _join_relative(root, profile.ledger_path)
    health_path_lexical = _join_relative(root, profile.health_path)
    allowed_root = allowed_root_lexical.resolve(strict=False)
    ledger_path = ledger_path_lexical.resolve(strict=False)
    health_path = health_path_lexical.resolve(strict=False)
    paths_are_canonical = (
        profile.allowed_root == CANONICAL_ALLOWED_ROOT and relative_paths_valid
    )
    paths_within_allowed_root = (
        _is_relative_to(ledger_path, allowed_root)
        and _is_relative_to(health_path, allowed_root)
        and ledger_path != health_path
        and ledger_path.suffix == ".jsonl"
        and health_path.suffix == ".json"
    )
    symlink_detected = any(
        _contains_existing_symlink(path, stop_at=root)
        for path in (allowed_root_lexical, ledger_path_lexical, health_path_lexical)
    )
    allowed_root_exists = allowed_root.exists()
    allowed_root_is_directory = allowed_root.is_dir()
    allowed_root_writable = (
        allowed_root_is_directory and os.access(allowed_root, os.W_OK)
    )

    blockers: list[str] = []
    if not root.is_dir():
        blockers.append("project_root_missing_or_not_directory")
    if not relative_paths_valid:
        blockers.append("unsafe_relative_path")
    if not paths_are_canonical:
        blockers.append("allowed_root_not_canonical")
    if not paths_within_allowed_root:
        blockers.append("output_path_outside_allowed_root")
    if symlink_detected:
        blockers.append("symlink_path_component_detected")
    if not allowed_root_exists:
        blockers.append("allowed_root_missing")
    elif not allowed_root_is_directory:
        blockers.append("allowed_root_not_directory")
    elif not allowed_root_writable:
        blockers.append("allowed_root_not_writable")

    return PathPolicyReportV1(
        status="blocked" if blockers else "ok",
        reason=blockers[0] if blockers else "path_policy_ok",
        project_root=str(root),
        allowed_root=str(allowed_root),
        ledger_path=str(ledger_path),
        health_path=str(health_path),
        allowed_root_exists=allowed_root_exists,
        allowed_root_is_directory=allowed_root_is_directory,
        allowed_root_writable=allowed_root_writable,
        symlink_detected=symlink_detected,
        paths_are_canonical=paths_are_canonical,
        paths_within_allowed_root=paths_within_allowed_root,
    )


def _is_safe_posix_relative_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _join_relative(root: Path, value: str) -> Path:
    if not _is_safe_posix_relative_path(value):
        return root / "__blocked_unsafe_path__"
    return root / Path(*PurePosixPath(value).parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _contains_existing_symlink(path: Path, *, stop_at: Path) -> bool:
    current = path
    while True:
        if current.exists() and current.is_symlink():
            return True
        if current == stop_at or current.parent == current:
            return False
        current = current.parent
