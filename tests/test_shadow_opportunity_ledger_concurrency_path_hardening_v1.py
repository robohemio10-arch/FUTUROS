from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from smartcrypto.research.shadow_opportunity_engine.persistence import (
    append_ledger_idempotent,
    resolve_ledger_path,
    resolve_report_path,
)


def test_equivalent_root_paths_resolve_to_same_ledger_target(tmp_path: Path) -> None:
    canonical = resolve_ledger_path(tmp_path, "data/reports/ledger.jsonl")
    aliased_root = tmp_path / "path-alias" / ".."
    aliased = resolve_ledger_path(aliased_root, "data/reports/ledger.jsonl")

    assert canonical == aliased


def test_concurrent_equivalent_roots_preserve_single_ledger_row(tmp_path: Path) -> None:
    canonical_root = tmp_path
    aliased_root = tmp_path / "path-alias" / ".."
    ledger_path = resolve_ledger_path(canonical_root, "data/reports/ledger.jsonl")
    row = {"ledger_id": "ledger-concurrent-path", "reason": "PAIR_OCCUPIED"}
    roots = [canonical_root, aliased_root, canonical_root, aliased_root]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda root: append_ledger_idempotent(root, ledger_path, [row]),
                roots,
            )
        )

    assert sum(results) == 1
    assert ledger_path.read_text(encoding="utf-8").splitlines() == [
        '{"ledger_id": "ledger-concurrent-path", "reason": "PAIR_OCCUPIED"}'
    ]


def test_restricted_paths_still_fail_closed_after_normalization(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"

    with pytest.raises(ValueError, match="output_must_be_under_data_reports"):
        resolve_report_path(tmp_path / "path-alias" / "..", outside)
