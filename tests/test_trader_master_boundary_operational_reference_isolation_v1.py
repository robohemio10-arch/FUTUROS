from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from smartcrypto.learning.paper_autolearning.master_consolidation import (
    build_paper_feedback_master_consolidation_report,
)


ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "config/paper_dashboard.yml",
    "config/paper_session.yml",
    "scripts/apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py",
    "smartcrypto/execution/paper_cycle_reset.py",
    "smartcrypto/learning/feature_contracts/contract_builder.py",
    "smartcrypto/learning/paper_autolearning/daily_foundation_runner.py",
    "smartcrypto/learning/paper_autolearning/master_consolidation.py",
)
APPLY_CLI = ROOT / "scripts" / "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py"


def test_operational_targets_do_not_reference_legacy_artifact() -> None:
    for relative in TARGETS:
        source = (ROOT / relative).read_text(encoding="utf-8").casefold()
        assert "data/trades/" + "trades_master" not in source


def test_operational_configuration_has_no_legacy_dataset_key() -> None:
    for relative in ("config/paper_dashboard.yml", "config/paper_session.yml"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "trades_master:" not in source


def test_retired_apply_cli_is_fail_closed(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(APPLY_CLI),
            "--package-dir",
            str(tmp_path / "package"),
            "--project-root",
            str(tmp_path),
            "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert report["decision"] == "LEGACY_DATASET_APPLY_FORBIDDEN"
    assert report["write_performed"] is False
    assert list(tmp_path.iterdir()) == []


def test_consolidation_is_blocked_without_side_effects(tmp_path: Path) -> None:
    report = build_paper_feedback_master_consolidation_report(
        project_root=tmp_path,
        source_path=tmp_path / "source.parquet",
        trades_master_xlsx_path=tmp_path / "legacy.xlsx",
        trades_master_parquet_path=tmp_path / "legacy.parquet",
        preview_json_path=tmp_path / "preview.json",
        backup_root=tmp_path / "backups",
        write_preview=True,
        write_master=True,
    )
    assert report["status"] == "blocked"
    assert report["write_performed"] is False
    assert report["master_write_performed"] is False
    assert report["operational_authority"] is False
    assert list(tmp_path.iterdir()) == []


def test_no_operational_consumer_is_registered_in_policy() -> None:
    policy = json.loads(
        (ROOT / "config/trader_master_legacy_research_only_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    registered = {item["relative_path"] for item in policy["registered_consumers"]}
    assert registered.isdisjoint(TARGETS)


def test_target_modules_do_not_import_exchange_or_order_components() -> None:
    for relative in TARGETS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for forbidden in ("ccxt", "create_order", "submit_order", "fetch_balance"):
            assert forbidden not in source


def test_apply_module_can_be_imported_without_side_effects(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("retired_apply", APPLY_CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert list(tmp_path.iterdir()) == []
