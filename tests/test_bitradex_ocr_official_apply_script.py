from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    ROOT / "scripts" / "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py"
)


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("apply_bitradex_ocr_v5", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def disabled_report(tmp_path: Path) -> dict[str, object]:
    module = load_module()
    args = module.parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--package-dir",
            str(tmp_path / "package"),
            "--no-write",
        ]
    )
    return module.build_disabled_report(args)


def test_official_apply_api_is_retired() -> None:
    module = load_module()

    assert not hasattr(module, "ApplyPaths")
    assert not hasattr(module, "apply_bitradex_ocr_orderid_synthetic_v5")


def test_disabled_report_is_fail_closed(tmp_path: Path) -> None:
    report = disabled_report(tmp_path)

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_official_dataset_apply_disabled"
    assert report["decision"] == "LEGACY_DATASET_APPLY_FORBIDDEN"
    assert report["import_authorized"] is False
    assert report["write_authorized"] is False
    assert report["write_performed"] is False


def test_disabled_report_preserves_safety_denials(tmp_path: Path) -> None:
    report = disabled_report(tmp_path)

    for field in (
        "backup_created",
        "writes_official_dataset",
        "writes_parquet",
        "writes_xlsx",
        "writes_csv",
        "writes_sqlite",
        "writes_runtime",
        "changes_training_dataset",
        "sends_orders",
        "changes_risk",
        "exchange_private_access",
        "operational_authority",
    ):
        assert report[field] is False


def test_cli_is_blocked_even_without_no_write_flag(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(tmp_path),
            "--package-dir",
            str(tmp_path / "package"),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["no_write"] is True
    assert payload["write_performed"] is False


def test_cli_does_not_create_or_mutate_files(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    before = sentinel.read_bytes()

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--project-root",
            str(tmp_path),
            "--package-dir",
            str(tmp_path / "missing-package"),
            "--no-write",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert sentinel.read_bytes() == before
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == [
        Path("sentinel.txt")
    ]


def test_retired_script_contains_no_writer_implementation() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "to_excel(",
        "to_parquet(",
        "ExcelWriter(",
        "shutil.copy",
        "write_bytes(",
        "write_text(",
    ):
        assert forbidden not in source
