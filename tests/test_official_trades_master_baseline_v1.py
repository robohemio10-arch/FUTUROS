from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.trades_master_official.baseline import (
    OfficialBaselineValidationError,
    build_official_trades_master_baseline,
)
from smartcrypto.research.trades_master_official.contracts import (
    CANONICAL_BASELINE_CONTRACT,
)
from smartcrypto.research.trades_master_official.loader import (
    OfficialMasterData,
    SourceAudit,
)
from smartcrypto.research.trades_master_official.persistence import (
    BASELINE_JSON,
    BASELINE_MD,
    BY_DURATION_CSV,
    BY_HOUR_CSV,
    BY_MONTH_CSV,
    BY_SYMBOL_SIDE_CSV,
    SHA256_MANIFEST_CSV,
    persist_baseline_reports,
)


def _frame() -> pd.DataFrame:
    economic = [9.0, -5.0, 7.0, -3.0]
    cumulative = [9.0, 4.0, 11.0, 8.0]
    peak = [9.0, 9.0, 11.0, 11.0]
    drawdown = [0.0, -5.0, 0.0, -3.0]

    return pd.DataFrame(
        {
            "trade_sequence": [1, 2, 3, 4],
            "order_id_raw": [
                "000000000000000000000001",
                "000000000000000000000002",
                "000000000000000000000003",
                "000000000000000000000004",
            ],
            "symbol": [
                "BTC_USDT",
                "BTC_USDT",
                "ETH_USDT",
                "ETH_USDT",
            ],
            "side": [
                "LONG",
                "SHORT",
                "LONG",
                "SHORT",
            ],
            "horario_abertura": [
                "2026-01-01 00:00:00",
                "2026-01-02 00:00:00",
                "2026-02-01 00:00:00",
                "SOURCE_NOT_AVAILABLE_FROM_PRINT",
            ],
            "horario_fechamento": [
                "2026-01-01 00:10:00",
                "2026-01-02 00:20:00",
                "2026-02-01 01:30:00",
                "2026-02-02 02:00:00",
            ],
            "fee_semantics_regime": [
                "STANDARD",
                "STANDARD",
                "STANDARD",
                "LEGACY",
            ],
            "reported_pnl": [
                10.0,
                -4.0,
                8.0,
                -2.0,
            ],
            "economic_fee_adjustment": [
                -1.0,
                -1.0,
                -1.0,
                -1.0,
            ],
            "economic_net_pnl": economic,
            "cumulative_economic_net_pnl": cumulative,
            "running_peak_economic_net_pnl": peak,
            "economic_drawdown_pnl": drawdown,
            "economic_pnl_canonicalized": [
                True,
                True,
                True,
                True,
            ],
            "final_dedup_keep": [
                True,
                True,
                True,
                True,
            ],
            "final_dedup_excluded": [
                False,
                False,
                False,
                False,
            ],
            "fee_semantics_gate": [
                "PASS",
                "PASS",
                "PASS",
                "PASS",
            ],
            "final_dedup_gate": [
                "PASS",
                "PASS",
                "PASS",
                "PASS",
            ],
            "trades_master_write_allowed": [
                False,
                False,
                False,
                False,
            ],
        }
    )


def _master() -> OfficialMasterData:
    frame = _frame()

    audit = SourceAudit(
        status="ok",
        master_path="fixture.xlsx",
        master_sha256="a" * 64,
        source_contract_sha256="b" * 64,
        rows=4,
        columns=41,
        sheets=("TRADES", "METADATA", "AUDIT"),
        trades_dimension="A1:AO5",
        formula_count=0,
        column_names=tuple(),
        economic_identity_max_abs_error=0.0,
        fee_regime_counts={
            "LEGACY": 1,
            "STANDARD": 3,
        },
        invalid_order_id_count=0,
        trade_sequence_contiguous=True,
        hash_unchanged_after_read=True,
        read_only=True,
        writes_master=False,
        sends_orders=False,
        changes_risk=False,
        operational_authority=False,
    )

    return OfficialMasterData(
        path=Path("fixture.xlsx"),
        frame=frame,
        audit=audit,
    )


def _contract():
    return replace(
        CANONICAL_BASELINE_CONTRACT,
        expected_rows=4,
        expected_reported_pnl_total=12.0,
        expected_fee_adjustment_total=-4.0,
        expected_economic_net_pnl_total=8.0,
        expected_profit_factor=2.0,
        expected_win_rate=0.5,
        expected_max_drawdown_signed=-5.0,
        expected_opening_available=3,
        expected_opening_unavailable=1,
        expected_standard_count=3,
        expected_legacy_count=1,
    )


def test_reproduces_baseline_metrics() -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    metrics = payload["global_metrics"]

    assert payload["engineering_status"] == "PASS"
    assert payload["official_baseline_status"] == "PASS"
    assert payload["decision"] == "BASELINE_REPRODUCED"
    assert metrics["economic_net_pnl_total"] == 8.0
    assert metrics["profit_factor"] == 2.0
    assert metrics["win_rate"] == 0.5
    assert metrics["max_drawdown_signed"] == -5.0


def test_keeps_quant_edge_status_separate() -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    assert (
        payload["quant_edge_status"]
        == "NOT_EVALUATED_WQ1"
    )
    assert payload["operational_authority"] is False
    assert payload["sends_orders"] is False
    assert payload["changes_risk"] is False


def test_classifies_opening_time_without_synthesis() -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    assert payload["opening_time"]["available"] == 3
    assert payload["opening_time"]["unavailable"] == 1
    assert (
        payload["opening_time"]["legacy_sentinel"]
        == "SOURCE_NOT_AVAILABLE_FROM_PRINT"
    )


def test_builds_expected_breakdowns() -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    assert len(payload["by_symbol_side"]) == 4
    assert {
        row["month"]
        for row in payload["by_month"]
    } == {"2026-01", "2026-02"}
    assert len(payload["by_duration_bucket"]) == 3


def test_rejects_noncanonicalized_pnl() -> None:
    master = _master()
    master.frame.loc[
        0,
        "economic_pnl_canonicalized",
    ] = False

    with pytest.raises(
        OfficialBaselineValidationError,
        match="economic_pnl_not_fully_canonicalized",
    ):
        build_official_trades_master_baseline(
            master,
            contract=_contract(),
        )


def test_rejects_unexpected_invalid_opening_time() -> None:
    master = _master()
    master.frame.loc[
        0,
        "horario_abertura",
    ] = "INVALID_TIMESTAMP"

    with pytest.raises(
        OfficialBaselineValidationError,
        match="unexpected_invalid_opening_timestamp",
    ):
        build_official_trades_master_baseline(
            master,
            contract=_contract(),
        )


def test_persists_only_expected_report_set(
    tmp_path: Path,
) -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    written = persist_baseline_reports(
        payload,
        output_dir=tmp_path,
    )

    expected = {
        BASELINE_JSON,
        BASELINE_MD,
        BY_SYMBOL_SIDE_CSV,
        BY_MONTH_CSV,
        BY_HOUR_CSV,
        BY_DURATION_CSV,
        SHA256_MANIFEST_CSV,
    }

    assert set(written) == expected
    assert {
        path.name
        for path in tmp_path.iterdir()
        if path.is_file()
    } == expected

    manifest = pd.read_csv(
        tmp_path / SHA256_MANIFEST_CSV
    )

    assert set(manifest["file"]) == (
        expected - {SHA256_MANIFEST_CSV}
    )
    assert manifest["sha256"].str.len().eq(64).all()


def test_report_persistence_is_deterministic(
    tmp_path: Path,
) -> None:
    payload = build_official_trades_master_baseline(
        _master(),
        contract=_contract(),
    )

    first = tmp_path / "first"
    second = tmp_path / "second"

    persist_baseline_reports(
        payload,
        output_dir=first,
    )
    persist_baseline_reports(
        payload,
        output_dir=second,
    )

    for filename in (
        BASELINE_JSON,
        BASELINE_MD,
        BY_SYMBOL_SIDE_CSV,
        BY_MONTH_CSV,
        BY_HOUR_CSV,
        BY_DURATION_CSV,
        SHA256_MANIFEST_CSV,
    ):
        assert (
            (first / filename).read_bytes()
            == (second / filename).read_bytes()
        )
