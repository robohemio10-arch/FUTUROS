from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.research.ocr_v11_dataset import (
    EXPECTED_MASTER_SHA256,
    SAFETY_FLAGS,
    build_from_paths,
    build_research_dataset,
    normalize_candles,
    normalize_trades,
    resolve_paths,
)
from smartcrypto.research.reporting import (
    build_alignment_summary,
    build_executive_summary,
    prepare_chart_data,
    render_executive_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_ocr_v11_research_dataset.py"


def trade_row(
    *,
    order_id: str = "aaaaaaaaaaaaaaaaaaaaaaaa",
    side: str = "long",
    open_time: str = "2026-06-01T10:00:30Z",
    close_time: str = "2026-06-01T10:03:30Z",
    entry_price: float = 100_000.0,
    exit_price: float = 102_000.0,
    net_pnl: float = 3.8,
) -> dict[str, Any]:
    return {
        "moeda": "BTCUSDT",
        "fechar_side": side,
        "order_id": order_id,
        "pnl_fechado": net_pnl,
        "taxa_lucros_perdas_fechados_pct": net_pnl,
        "preco_abertura": entry_price,
        "preco_fechamento": exit_price,
        "volume_posicao": 0.002,
        "volume_fechado": 0.002,
        "horario_abertura": open_time,
        "horario_fechamento": close_time,
        "taxa_1": -0.2,
        "taxa_2": None,
        "source_file": "fixture.png",
        "_dedup_key": f"trade-{order_id}",
    }


def candle_frame(*, missing_timestamp: str | None = None) -> pd.DataFrame:
    timestamps = pd.date_range("2026-06-01T09:00:00Z", periods=70, freq="1min")
    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        close = 99_000.0 + index * 10.0
        rows.append(
            {
                "symbol": "BTCUSDT",
                "pair": "BTC/USDT:USDT",
                "tf": "1m",
                "ts": timestamp,
                "open": close - 100.0,
                "high": close + 200.0,
                "low": close - 200.0,
                "close": close,
                "volume": 10.0 + index,
            }
        )
    path_values = {
        "2026-06-01T10:00:00+00:00": (100_000.0, 101_000.0, 99_000.0, 100_500.0),
        "2026-06-01T10:01:00+00:00": (100_500.0, 104_000.0, 98_000.0, 102_000.0),
        "2026-06-01T10:02:00+00:00": (102_000.0, 103_000.0, 97_000.0, 101_000.0),
        "2026-06-01T10:03:00+00:00": (101_000.0, 102_000.0, 99_000.0, 102_000.0),
    }
    for row in rows:
        key = pd.Timestamp(row["ts"]).isoformat()
        if key in path_values:
            row["open"], row["high"], row["low"], row["close"] = path_values[key]
    frame = pd.DataFrame(rows)
    if missing_timestamp:
        missing = pd.Timestamp(missing_timestamp)
        frame = frame[frame["ts"].ne(missing)].reset_index(drop=True)
    return frame


def build_dataset(
    trade: dict[str, Any],
    *,
    missing_timestamp: str | None = None,
) -> pd.DataFrame:
    normalized_trades = normalize_trades(pd.DataFrame([trade]), None)
    normalized_candles, invalid = normalize_candles(
        candle_frame(missing_timestamp=missing_timestamp)
    )
    assert invalid == 0
    return build_research_dataset(normalized_trades, normalized_candles)


def write_fixture_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    master = project / "data" / "trades" / "trades_master.xlsx"
    candles = project / "data" / "features" / "market_features_60d.parquet"
    master.parent.mkdir(parents=True)
    candles.parent.mkdir(parents=True)
    pd.DataFrame([trade_row()]).to_excel(master, index=False)
    candle_frame().to_parquet(candles, index=False)
    return project, master, candles


def test_default_no_write_does_not_materialize_outputs(tmp_path: Path) -> None:
    project, _, _ = write_fixture_project(tmp_path)
    paths = resolve_paths(project)

    result = build_from_paths(paths)

    assert result.report["status"] == "ok"
    assert result.report["write_performed"] is False
    assert not paths.output_path.exists()
    assert not paths.report_path.exists()
    assert not paths.executive_summary_path.exists()
    assert not paths.executive_markdown_path.exists()


def test_write_materializes_parquet_json_and_executive_reports(tmp_path: Path) -> None:
    project, _, _ = write_fixture_project(tmp_path)
    paths = resolve_paths(project)

    result = build_from_paths(
        paths,
        write=True,
        analysis_date_utc="2026-06-23T12:00:00Z",
    )

    assert result.report["write_performed"] is True
    assert len(pd.read_parquet(paths.output_path)) == 1
    assert json.loads(paths.report_path.read_text(encoding="utf-8"))["status"] == "ok"
    executive = json.loads(paths.executive_summary_path.read_text(encoding="utf-8"))
    markdown = paths.executive_markdown_path.read_text(encoding="utf-8")
    assert executive["analysis_date_utc"] == "2026-06-23T12:00:00Z"
    assert executive["trades"] == 1
    assert "# OCR V1.1 Research Dataset" in markdown
    assert "Qualidade do candle alignment" in markdown


def test_master_is_never_modified(tmp_path: Path) -> None:
    project, master, _ = write_fixture_project(tmp_path)
    before = master.read_bytes()
    paths = resolve_paths(project)

    build_from_paths(paths, write=True, analysis_date_utc="2026-06-23T12:00:00Z")

    assert master.read_bytes() == before


def test_safety_flags_are_preserved(tmp_path: Path) -> None:
    project, _, _ = write_fixture_project(tmp_path)
    report = build_from_paths(resolve_paths(project)).report
    for key, expected in SAFETY_FLAGS.items():
        assert report[key] is expected


def test_long_candle_alignment_and_mfe_mae_are_correct() -> None:
    dataset = build_dataset(trade_row())
    row = dataset.iloc[0]

    assert row["entry_candle_timestamp"] == pd.Timestamp("2026-06-01T10:00:00Z")
    assert row["exit_candle_timestamp"] == pd.Timestamp("2026-06-01T10:03:00Z")
    assert row["entry_candle_found"] is True or bool(row["entry_candle_found"])
    assert row["exit_candle_found"] is True or bool(row["exit_candle_found"])
    assert row["candles_between_count"] == 4
    assert row["missing_candle_count"] == 0
    assert row["candle_alignment_status"] == "aligned"
    assert row["mfe_abs"] == pytest.approx(4_000.0)
    assert row["mae_abs"] == pytest.approx(-3_000.0)
    assert row["mfe_pct"] == pytest.approx(4.0)
    assert row["mae_pct"] == pytest.approx(-3.0)
    assert row["max_favorable_price"] == pytest.approx(104_000.0)
    assert row["max_adverse_price"] == pytest.approx(97_000.0)
    assert row["time_to_mfe_seconds"] == pytest.approx(30.0)
    assert row["time_to_mae_seconds"] == pytest.approx(90.0)


def test_short_candle_alignment_and_mfe_mae_are_correct() -> None:
    dataset = build_dataset(
        trade_row(side="short", exit_price=98_000.0, net_pnl=3.8)
    )
    row = dataset.iloc[0]

    assert row["candle_alignment_status"] == "aligned"
    assert row["mfe_abs"] == pytest.approx(3_000.0)
    assert row["mae_abs"] == pytest.approx(-4_000.0)
    assert row["mfe_pct"] == pytest.approx(3.0)
    assert row["mae_pct"] == pytest.approx(-4.0)
    assert row["max_favorable_price"] == pytest.approx(97_000.0)
    assert row["max_adverse_price"] == pytest.approx(104_000.0)


@pytest.mark.parametrize(
    ("side", "exit_price", "expected_opposite"),
    [("long", 102_000.0, -4.2), ("short", 98_000.0, -4.2)],
)
def test_opposite_side_counterfactual_is_post_trade_only(
    side: str,
    exit_price: float,
    expected_opposite: float,
) -> None:
    row = build_dataset(
        trade_row(side=side, exit_price=exit_price, net_pnl=3.8)
    ).iloc[0]
    assert row["opposite_side_pnl_estimate"] == pytest.approx(expected_opposite)
    assert row["opposite_side_would_win"] == 0
    assert row["actual_side_vs_opposite_delta"] == pytest.approx(8.0)


def test_missing_candle_blocks_research_eligibility() -> None:
    row = build_dataset(
        trade_row(),
        missing_timestamp="2026-06-01T10:02:00Z",
    ).iloc[0]
    assert row["missing_candle_count"] == 1
    assert row["candle_alignment_status"] == "missing_or_partial"
    assert not bool(row["is_research_eligible"])
    assert "missing_or_partial_candles" in row["research_block_reason"]


def test_entry_features_use_only_fully_closed_prior_candle() -> None:
    candles = candle_frame()
    expected_previous_close = float(
        candles.loc[candles["ts"].eq(pd.Timestamp("2026-06-01T09:59:00Z")), "close"].iloc[0]
    )
    candles.loc[candles["ts"].eq(pd.Timestamp("2026-06-01T10:00:00Z")), "close"] = 199_000.0
    normalized_candles, _ = normalize_candles(candles)
    trades = normalize_trades(pd.DataFrame([trade_row()]), None)

    row = build_research_dataset(trades, normalized_candles).iloc[0]

    assert row["entry_feature_timestamp"] == pd.Timestamp("2026-06-01T09:59:00Z")
    assert row["entry_close"] == pytest.approx(expected_previous_close)
    assert row["entry_close"] != 199_000.0


def test_expected_sha_and_resource_configuration_are_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, master, _ = write_fixture_project(tmp_path)
    monkeypatch.setenv("SMARTCRYPTO_TRAINING_WORKERS", "10")
    monkeypatch.setenv("SMARTCRYPTO_TRAINING_MAX_RAM_GB", "16")

    report = build_from_paths(resolve_paths(project)).report

    assert report["expected_master_sha256"] == EXPECTED_MASTER_SHA256
    assert report["source_master_sha256"] == hashlib.sha256(master.read_bytes()).hexdigest()
    assert report["master_sha256_matches_expected"] is False
    assert report["configured_workers"] == 10
    assert report["configured_max_ram_gb"] == 16.0


def test_report_structural_fields_are_deterministic(tmp_path: Path) -> None:
    project, _, _ = write_fixture_project(tmp_path)
    paths = resolve_paths(project)

    first = build_from_paths(paths).report
    second = build_from_paths(paths).report

    assert first == second


def test_reporting_helpers_are_pure_and_prepare_chart_contract() -> None:
    dataset = build_dataset(trade_row())
    before = dataset.copy(deep=True)
    technical = {
        "status": "ok",
        "source_master_path": "master.xlsx",
        "min_open_time": "2026-06-01T10:00:30Z",
        "max_close_time": "2026-06-01T10:03:30Z",
    }

    alignment = build_alignment_summary(dataset)
    chart_data = prepare_chart_data(dataset)
    summary = build_executive_summary(
        dataset,
        technical,
        analysis_date_utc="2026-06-23T12:00:00Z",
    )
    markdown = render_executive_markdown(summary)

    pd.testing.assert_frame_equal(dataset, before)
    assert alignment["aligned_trades"] == 1
    assert chart_data["trades_by_symbol"] == [{"symbol": "BTCUSDT", "trades": 1}]
    assert summary["eligible_trades"] == 1
    assert "Conclusão" in markdown


def test_cli_no_write_and_write_modes_are_controlled(tmp_path: Path) -> None:
    project, master, candles = write_fixture_project(tmp_path)
    output = project / "data" / "research" / "output.parquet"
    report = project / "data" / "reports" / "audit.json"
    executive_dir = project / "data" / "reports" / "training_reports"
    common = [
        sys.executable,
        str(SCRIPT),
        "--project-root",
        str(project),
        "--master",
        str(master),
        "--candles",
        str(candles),
        "--output",
        str(output),
        "--report",
        str(report),
        "--executive-reports-dir",
        str(executive_dir),
        "--json",
    ]
    dry = subprocess.run(
        [*common, "--no-write"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    dry_payload = json.loads(dry.stdout)
    assert dry.returncode == 0
    assert dry_payload["write_performed"] is False
    assert not output.exists()
    assert not report.exists()

    write = subprocess.run(
        [*common, "--write"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    write_payload = json.loads(write.stdout)
    assert write.returncode == 0, write.stderr
    assert write_payload["write_performed"] is True
    assert output.exists()
    assert report.exists()
    assert (executive_dir / "ocr_v11_research_dataset_summary.json").exists()
    assert (executive_dir / "ocr_v11_research_dataset_executive.md").exists()


def test_module_does_not_import_operational_or_private_dependencies() -> None:
    paths = [
        ROOT / "smartcrypto" / "research" / "ocr_v11_dataset.py",
        ROOT / "smartcrypto" / "research" / "reporting.py",
        SCRIPT,
    ]
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    forbidden = ("freqtrade", "ccxt", "docker", "smartcrypto.execution", "smartcrypto.risk")
    assert not any(module.startswith(forbidden) for module in imported)
