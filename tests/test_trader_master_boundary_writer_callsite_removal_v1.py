from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.data import trade_file_readonly
from smartcrypto.data.trades_importer import (
    LegacyMasterImportDisabledError,
    import_trades_incrementally,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    FindingClassification,
    analyze_python_source,
)


ROOT = Path(__file__).resolve().parents[1]
IMPORT_CLI = ROOT / "scripts" / "import_trades_incremental.py"
QUALITY_GATE = ROOT / "scripts" / "large_trades_import_quality_gate.py"
IMPORTER = ROOT / "smartcrypto" / "data" / "trades_importer.py"
FINGERPRINT_SPEC = (
    ROOT / "smartcrypto" / "data" / "trader_master_fingerprint_v2" / "fingerprint_spec.py"
)
POLICY = ROOT / "config" / "trader_master_legacy_research_only_policy_v1.json"
GUARDED_EXECUTOR = (
    ROOT / "smartcrypto/data/bitradex_ocr_legacy_authorized_append/executor.py"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_quality_gate():
    spec = importlib.util.spec_from_file_location("quality_gate_writer_removal", QUALITY_GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_trade(order_id: str = "paper-1") -> dict[str, str]:
    return {
        "moeda": "BTCUSDT",
        "fechar_side": "LONG",
        "leverage": "1",
        "order_id": order_id,
        "pnl_fechado": "1",
        "taxa_lucros_perdas_fechados_pct": "1",
        "preco_abertura": "100",
        "preco_fechamento": "101",
        "volume_posicao": "1",
        "volume_fechado": "1",
        "horario_abertura": "2026-01-01 00:00:00",
        "horario_fechamento": "2026-01-01 00:01:00",
        "taxa_1": "0",
        "preco_transacao": "101",
        "volume_transacao": "1",
        "direcao_liquidez": "TAKER",
        "taxa_2": "0",
        "horario_transacao": "2026-01-01 00:01:00",
    }


def test_production_modules_do_not_import_legacy_writer_api() -> None:
    completed = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "from smartcrypto.data.trades_importer import",
            "--",
            "*.py",
            ":(exclude)tests/**",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""


def test_import_cli_is_fail_closed_without_filesystem_writes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(IMPORT_CLI),
            "--inbox-dir",
            str(tmp_path / "inbox"),
            "--processed-dir",
            str(tmp_path / "processed"),
            "--report",
            str(tmp_path / "report.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert report["decision"] == "LEGACY_MASTER_IMPORT_FORBIDDEN"
    assert report["write_performed"] is False
    assert list(tmp_path.iterdir()) == []


def test_legacy_import_function_fails_before_any_side_effect(tmp_path: Path) -> None:
    with pytest.raises(LegacyMasterImportDisabledError):
        import_trades_incrementally(
            inbox_dir=tmp_path / "inbox",
            master_xlsx_path=tmp_path / "master.xlsx",
            master_parquet_path=tmp_path / "master.parquet",
            compatibility_xlsx_path=tmp_path / "compat.xlsx",
            processed_dir=tmp_path / "processed",
            report_path=tmp_path / "report.json",
        )
    assert list(tmp_path.iterdir()) == []


def test_trade_file_readonly_has_no_write_or_directory_api() -> None:
    source = Path(trade_file_readonly.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "trades_importer",
        ".mkdir(",
        ".to_parquet(",
        ".to_excel(",
        ".to_csv(",
        "shutil.",
        "sqlite3",
    ):
        assert forbidden not in source


def test_trade_file_readonly_preserves_source_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    pd.DataFrame([sample_trade()]).to_csv(source, index=False)
    before = sha256(source)
    frame = trade_file_readonly.read_trade_file(source)
    cleaned = trade_file_readonly.clean_trade_frame(frame, source_file=source.name)
    assert len(cleaned) == 1
    assert sha256(source) == before


def test_quality_gate_apply_is_blocked_without_master_mutation(tmp_path: Path) -> None:
    module = load_quality_gate()
    source = tmp_path / "source.parquet"
    master = tmp_path / "trades" / "trades_master.parquet"
    source.parent.mkdir(parents=True, exist_ok=True)
    master.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([sample_trade("new")]).to_parquet(source, index=False)
    pd.DataFrame([sample_trade("old")]).to_parquet(master, index=False)
    before = sha256(master)
    report = module.run_quality_gate(
        source_file=source,
        master_xlsx_path=master.with_suffix(".xlsx"),
        master_parquet_path=master,
        compatibility_xlsx_path=tmp_path / "trades" / "compat.xlsx",
        report_path=tmp_path / "reports" / "report.json",
        backup_dir=tmp_path / "backups",
        apply=True,
    )
    assert report["reason"] == "legacy_master_apply_forbidden"
    assert report["backup_created"] is False
    assert report["writes_trader_master"] is False
    assert sha256(master) == before
    assert not (tmp_path / "backups").exists()


def test_quarantined_importer_has_no_external_production_callsites() -> None:
    source = IMPORTER.read_text(encoding="utf-8")
    assert "class LegacyMasterImportDisabledError" in source
    assert "def write_master" in source
    assert "raise LegacyMasterImportDisabledError" in source


def test_protected_contract_files_are_unchanged() -> None:
    assert sha256(FINGERPRINT_SPEC) == (
        "7efee2c2ac682242796ac9954ddea525cd34c4a69ab985cdefcdb4e5fe223147"
    )
    assert sha256(POLICY) == (
        "b6723f604e51559a96cc3f71032d74145d38214f5fa0ba772fb4a00262203751"
    )


def test_guarded_transition_is_not_a_generic_legacy_writer_exception() -> None:
    source = GUARDED_EXECUTOR.read_text(encoding="utf-8")
    findings = analyze_python_source(
        "smartcrypto/data/bitradex_ocr_legacy_authorized_append/executor.py",
        source,
    )
    assert all(
        item.classification
        != FindingClassification.AUTHORIZED_GUARDED_TRANSITION_IMPLEMENTATION
        for item in findings
    )


def test_uncontracted_writer_remains_blocking() -> None:
    findings = analyze_python_source(
        "smartcrypto/data/uncontracted_writer.py",
        "from pathlib import Path\n"
        "Path('data/trades/trades_master.parquet').write_bytes(b'unsafe')\n",
    )
    assert any(item.severity.value in {"critical", "high"} for item in findings)
