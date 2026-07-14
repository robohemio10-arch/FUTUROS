from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.learning.paper_autolearning.master_consolidation import (
    build_paper_feedback_master_consolidation_report,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("write_preview", "write_master"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_retired_consolidation_is_always_fail_closed(
    tmp_path: Path,
    write_preview: bool,
    write_master: bool,
) -> None:
    report = build_paper_feedback_master_consolidation_report(
        project_root=tmp_path,
        source_path=tmp_path / "source.parquet",
        trades_master_xlsx_path=tmp_path / "legacy.xlsx",
        trades_master_parquet_path=tmp_path / "legacy.parquet",
        preview_json_path=tmp_path / "preview.json",
        preview_markdown_path=tmp_path / "preview.md",
        backup_root=tmp_path / "backups",
        write_preview=write_preview,
        write_master=write_master,
    )
    assert report["status"] == "blocked"
    assert report["decision"] == "LEGACY_DATASET_CONSOLIDATION_FORBIDDEN"
    assert report["write_performed"] is False
    assert report["master_write_performed"] is False
    assert report["backup_created"] is False
    assert list(tmp_path.iterdir()) == []


def test_retired_consolidation_preserves_safety_flags(tmp_path: Path) -> None:
    report = build_paper_feedback_master_consolidation_report(project_root=tmp_path)
    for key in (
        "import_authorized",
        "write_authorized",
        "operational_authority",
        "writes_parquet",
        "writes_xlsx",
        "writes_csv",
        "writes_sqlite",
        "writes_runtime",
        "sends_orders",
        "changes_risk",
        "exchange_private_access",
    ):
        assert report[key] is False


def test_cli_is_blocked_and_does_not_materialize_outputs(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_paper_feedback_master_consolidation_v1.py",
            "--project-root",
            str(tmp_path),
            "--source",
            str(tmp_path / "source.parquet"),
            "--trades-master-xlsx",
            str(tmp_path / "legacy.xlsx"),
            "--trades-master-parquet",
            str(tmp_path / "legacy.parquet"),
            "--preview-json",
            str(tmp_path / "preview.json"),
            "--preview-markdown",
            str(tmp_path / "preview.md"),
            "--write-preview",
            "--write-master",
            "--json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert report["status"] == "blocked"
    assert report["write_performed"] is False
    assert list(tmp_path.iterdir()) == []


def test_module_has_no_filesystem_writer_calls() -> None:
    source = (
        ROOT
        / "smartcrypto"
        / "learning"
        / "paper_autolearning"
        / "master_consolidation.py"
    ).read_text(encoding="utf-8")
    for forbidden in (".mkdir(", ".to_parquet(", ".to_excel(", "shutil.", "sqlite3"):
        assert forbidden not in source
