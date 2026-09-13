"""Epoch/activation-scoped, serialized atomic append-by-identity research store."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from smartcrypto.runtime.integrity_traceability_v2 import AtomicWritePolicy, atomic_write_json

from .activation import read_object, safe_path
from .contracts import EvidenceError, Identity, check_identity, digest

SCHEMA = "qlib_v3_prospective_evidence_store_v1"


def location(root: Path, identity: Identity) -> Path:
    if not re.fullmatch(r"qlib-v3-[a-z0-9-]+", identity.epoch_id) or not re.fullmatch(
        r"[0-9a-f]{64}", identity.activation_sha256
    ):
        raise EvidenceError("unsafe_store_identity")
    return safe_path(root / "data/research/qlib_v3/prospective_evidence" /
                     identity.epoch_id / identity.activation_sha256 / "evidence.json")


def load(path: Path, identity: Identity) -> dict[str, Any]:
    safe_path(path)
    if not path.exists():
        return {"schema_version": SCHEMA, "identity": identity.mapping(),
                "signals": [], "outcomes": []}
    state = read_object(path)
    if state.get("schema_version") != SCHEMA:
        raise EvidenceError("store_not_v3")
    check_identity(state.get("identity"), identity)
    if state.get("store_sha256") != digest({k: v for k, v in state.items() if k != "store_sha256"}):
        raise EvidenceError("store_hash_mismatch")
    if not isinstance(state.get("signals"), list) or not isinstance(state.get("outcomes"), list):
        raise EvidenceError("store_records_invalid")
    if any(not isinstance(row, dict) for row in state["signals"] + state["outcomes"]):
        raise EvidenceError("store_record_not_object")
    return state


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent)
    guard = path.with_suffix(".guard")
    safe_path(guard)
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvidenceError("store_busy_or_stale_guard_requires_review") from exc
    try:
        yield
    finally:
        os.close(fd)
        guard.unlink()


def persist(path: Path, state: dict[str, Any]) -> None:
    safe_path(path)
    payload = {k: v for k, v in state.items() if k != "store_sha256"}
    payload["store_sha256"] = digest(payload)
    atomic_write_json(path, payload, allow_nan=False,
                      policy=AtomicWritePolicy.restricted([path.parent]))
