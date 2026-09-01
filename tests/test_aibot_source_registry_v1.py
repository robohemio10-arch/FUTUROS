from __future__ import annotations

from pathlib import Path

import pytest

from smartcrypto.research.aibot_parity.contracts import SOURCE_INVESTMENT_ID
from smartcrypto.research.aibot_parity.source_registry import (
    SourceRegistryError,
    build_source_record,
)


def test_same_artifact_has_deterministic_batch_id(tmp_path: Path) -> None:
    source = tmp_path / "data" / "trades" / "master.csv"
    source.parent.mkdir(parents=True)
    source.write_text("order_id,pnl\n1,2\n", encoding="utf-8")

    first = build_source_record(
        project_root=tmp_path,
        artifact_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_row_count=1,
    )
    second = build_source_record(
        project_root=tmp_path,
        artifact_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_row_count=1,
    )

    assert first.source_batch_id == second.source_batch_id
    assert first.source_artifact_sha256 == second.source_artifact_sha256
    assert first.source_batch_id.endswith(first.source_artifact_sha256)


def test_changed_artifact_creates_new_batch_for_same_investment(tmp_path: Path) -> None:
    source = tmp_path / "master.csv"
    source.write_text("order_id,pnl\n1,2\n", encoding="utf-8")
    first = build_source_record(
        project_root=tmp_path,
        artifact_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_row_count=1,
    )
    source.write_text("order_id,pnl\n1,3\n", encoding="utf-8")
    second = build_source_record(
        project_root=tmp_path,
        artifact_path=source,
        source_investment_id=SOURCE_INVESTMENT_ID,
        source_row_count=1,
    )

    assert first.source_batch_id != second.source_batch_id
    assert first.source_investment_id == second.source_investment_id
    assert second.source_investment_id == SOURCE_INVESTMENT_ID


def test_source_investment_id_is_not_inferred_from_filename(tmp_path: Path) -> None:
    source = tmp_path / "AIBOT_INVESTMENT_001.csv"
    source.write_text("value\n1\n", encoding="utf-8")

    with pytest.raises(SourceRegistryError, match="source_investment_id_mismatch"):
        build_source_record(
            project_root=tmp_path,
            artifact_path=source,
            source_investment_id="ANOTHER_INVESTMENT",
            source_row_count=1,
        )
