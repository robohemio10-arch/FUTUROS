"""Create-once external session, immutable bounded segments and atomic manifest."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWritePolicy,
    atomic_write_json,
)

from .contracts import EventRecord, canonical_sha256, schema_sha256


def validate_external_root(path: Path, forbidden_roots: tuple[Path, ...]) -> Path:
    if not path.is_absolute():
        raise ValueError("archive_root_must_be_absolute")
    for part in (path, *path.parents):
        if part.is_symlink() or (
            part.exists()
            and getattr(part.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            raise ValueError("archive_reparse_or_symlink_forbidden")
    root = path.resolve()
    if any((ancestor / ".git").exists() for ancestor in (root, *root.parents)):
        raise ValueError("archive_inside_git_forbidden")
    if any(root.is_relative_to(value.resolve()) for value in forbidden_roots):
        raise ValueError("archive_inside_project_or_runtime_forbidden")
    return root


class ExternalArchive:
    def __init__(
        self,
        root: Path,
        *,
        authorized: bool,
        forbidden_roots: tuple[Path, ...],
        segment_records: int,
        max_bytes: int,
    ) -> None:
        if not authorized:
            raise ValueError("explicit_archive_authorization_required")
        if not 1 <= segment_records <= 256 or not 1024 <= max_bytes <= 2 * 1024**3:
            raise ValueError("invalid_archive_bounds")
        self.root = validate_external_root(root, forbidden_roots)
        self.forbidden_roots = forbidden_roots
        self.session = self.root / f"l1-{uuid4().hex}"
        self.write_performed = False
        self.opened = False
        self.closed = False
        self.policy = AtomicWritePolicy.restricted([self.session], lock_timeout_seconds=1)
        self.segment_records = segment_records
        self.max_bytes = max_bytes
        self.pending: list[dict[str, Any]] = []
        self.segments: list[dict[str, Any]] = []
        self.bytes_written = 0
        self.previous_sha256 = "0" * 64

    def _open(self) -> None:
        if self.closed:
            raise ValueError("archive_already_closed")
        if self.opened:
            return
        validate_external_root(self.session, self.forbidden_roots)
        # Conservatively report a write attempt even if mkdir partially fails.
        self.write_performed = True
        self.session.mkdir(parents=True, exist_ok=False)
        atomic_write_json(
            self.session / "session.json",
            {
                "schema_version": "execution_decision_l1_archive_session_v1",
                "schema_sha256": schema_sha256(),
                "completion_authority": "manifest.json",
            },
            policy=self.policy,
            allow_nan=False,
        )
        self.opened = True

    def record(self, record: EventRecord) -> None:
        self._open()
        self.pending.append(record.model_dump(mode="json"))
        if len(self.pending) >= self.segment_records:
            self.flush()

    def flush(self) -> None:
        self._open()
        if not self.pending:
            return
        validate_external_root(self.session, self.forbidden_roots)
        payload = {
            "schema_version": "execution_decision_l1_archive_segment_v1",
            "schema_sha256": schema_sha256(),
            "segment_index": len(self.segments),
            "previous_segment_sha256": self.previous_sha256,
            "records": self.pending,
        }
        segment_hash = canonical_sha256(payload)
        payload["segment_sha256"] = segment_hash
        size = len(
            (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        )
        if self.bytes_written + size > self.max_bytes:
            raise ValueError("archive_byte_budget_exceeded")
        path = self.session / f"segment-{len(self.segments):06d}.json"
        if path.exists():
            raise ValueError("archive_segment_already_exists")
        result = atomic_write_json(path, payload, policy=self.policy, allow_nan=False)
        raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.segments.append(
            {
                "filename": path.name,
                "sha256": raw_hash,
                "segment_sha256": segment_hash,
                "records": len(self.pending),
            }
        )
        self.previous_sha256 = segment_hash
        self.bytes_written += result.bytes_written
        self.pending = []

    def close(self, summary: dict[str, Any]) -> None:
        self.flush()
        validate_external_root(self.session, self.forbidden_roots)
        payload = {
            "schema_version": "execution_decision_l1_archive_manifest_v1",
            "schema_sha256": schema_sha256(),
            "segments": self.segments,
            "bytes_written": self.bytes_written,
            "summary": summary,
        }
        payload["manifest_sha256"] = canonical_sha256(payload)
        atomic_write_json(
            self.session / "manifest.json", payload, policy=self.policy, allow_nan=False
        )
        self.closed = True
