from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.paper_edge_foundation import foundation
from smartcrypto.research.paper_edge_foundation.foundation import (
    build_paper_edge_foundation_v1,
    build_score_calibration,
    prepare_closed_trades,
    read_authoritative_paper_source,
)


SCRIPT_PATH = Path("scripts/build_paper_edge_foundation_v1.py")


def lineage_before_entry(trade_id: int) -> str:
    opened = pd.Timestamp("2026-05-01T00:00:00Z") + pd.Timedelta(days=trade_id)
    return (opened - pd.Timedelta(minutes=1)).isoformat()


def create_paper_db(
    path: Path,
    *,
    closed_count: int = 12,
    open_count: int = 2,
    null_closed_date: bool = False,
) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            is_open INTEGER NOT NULL,
            pair TEXT NOT NULL,
            is_short INTEGER NOT NULL,
            open_date TEXT NOT NULL,
            close_date TEXT,
            close_profit_abs REAL,
            close_profit REAL,
            stake_amount REAL NOT NULL,
            max_stake_amount REAL,
            open_trade_value REAL,
            leverage REAL,
            open_rate REAL NOT NULL,
            close_rate REAL,
            max_rate REAL,
            min_rate REAL,
            fee_open REAL,
            fee_open_cost REAL,
            fee_close REAL,
            fee_close_cost REAL,
            funding_fees REAL,
            funding_fee_running REAL,
            realized_profit REAL,
            amount REAL,
            exit_reason TEXT,
            strategy TEXT,
            enter_tag TEXT,
            timeframe INTEGER
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            ft_trade_id INTEGER NOT NULL,
            ft_order_side TEXT NOT NULL,
            ft_is_open INTEGER NOT NULL,
            status TEXT,
            filled REAL,
            remaining REAL,
            order_id TEXT NOT NULL,
            ft_order_tag TEXT
        );
        """
    )
    order_id = 1
    for trade_id in range(1, closed_count + 1):
        opened = pd.Timestamp("2026-05-01T00:00:00Z") + pd.Timedelta(days=trade_id)
        closed = opened + pd.Timedelta(minutes=10 * trade_id)
        if null_closed_date and trade_id == 1:
            close_value = None
        else:
            close_value = closed.isoformat()
        is_short = trade_id % 2
        pnl = (10.0 if trade_id % 3 == 1 else -5.0 if trade_id % 3 == 2 else 0.0)
        pair = "BTC/USDT:USDT" if trade_id % 2 else "ETH/USDT:USDT"
        connection.execute(
            """
            INSERT INTO trades VALUES (
                ?, 0, ?, ?, ?, ?, ?, ?, 100.0, 120.0, 100.0, 2.0,
                100.0, 101.0, 110.0, 90.0, 0.001, 0.2, 0.001, 0.3,
                -0.1, -0.1, ?, 1.0, ?, 'SmartCryptoSignalStrategy',
                'entry_signal', 5
            )
            """,
            (
                trade_id,
                pair,
                is_short,
                opened.isoformat(),
                close_value,
                pnl,
                pnl / 100.0,
                pnl,
                "roi" if pnl > 0 else "exit_signal",
            ),
        )
        entry_side = "sell" if is_short else "buy"
        exit_side = "buy" if is_short else "sell"
        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, 0, 'closed', 1.0, 0.0, ?, 'entry')",
            (order_id, trade_id, entry_side, f"entry-{trade_id}"),
        )
        order_id += 1
        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, 0, 'closed', 1.0, 0.0, ?, 'roi')",
            (order_id, trade_id, exit_side, f"exit-{trade_id}"),
        )
        order_id += 1
    for offset in range(open_count):
        trade_id = closed_count + offset + 1
        opened = pd.Timestamp("2026-08-01T00:00:00Z") + pd.Timedelta(days=offset)
        connection.execute(
            """
            INSERT INTO trades VALUES (
                ?, 1, 'BTC/USDT:USDT', 0, ?, NULL, NULL, NULL, 100.0,
                100.0, 100.0, 1.0, 100.0, NULL, 101.0, 99.0, 0.001,
                0.1, 0.001, NULL, 0.0, 0.0, 0.0, 1.0, NULL,
                'SmartCryptoSignalStrategy', 'entry_signal', 5
            )
            """,
            (trade_id, opened.isoformat()),
        )
    connection.commit()
    connection.close()
    return path


def build(tmp_path: Path, **overrides: object) -> dict[str, object]:
    db = create_paper_db(tmp_path / "paper.sqlite")
    kwargs: dict[str, object] = {
        "project_root": tmp_path,
        "paper_db": db,
    }
    kwargs.update(overrides)
    return build_paper_edge_foundation_v1(**kwargs)


def write_score_source(path: Path, count: int, *, out_of_range: bool = False) -> Path:
    rows = []
    for trade_id in range(1, count + 1):
        score = trade_id / (count + 1)
        if out_of_range and trade_id == count:
            score = 1.5
        financial_probability = 0.95 if trade_id % 3 == 1 else 0.05
        if out_of_range and trade_id == count:
            financial_probability = 1.5
        rows.append(
            {
                "trade_id": trade_id,
                "lineage_timestamp_utc": lineage_before_entry(trade_id),
                "financial_win_probability": financial_probability,
                "prob_up": score,
                "qlib_score": float(trade_id),
                "signal_confidence": score,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_regime_source(
    path: Path,
    count: int,
    *,
    regime_column: str = "entry_market_regime",
) -> Path:
    rows = []
    for trade_id in range(1, count + 1):
        if trade_id % 3 == 0:
            regime = "range"
        elif trade_id % 4 == 1:
            regime = "trend_up"
        elif trade_id % 2:
            regime = "trend_down"
        else:
            regime = "trend_up"
        rows.append(
            {
                "trade_id": trade_id,
                "lineage_timestamp_utc": lineage_before_entry(trade_id),
                regime_column: regime,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def load_script():
    spec = importlib.util.spec_from_file_location("build_paper_edge_foundation_v1_cli", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sqlite_mode_ro_preserves_bytes_and_hash(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    before = db.read_bytes()

    report = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)

    assert report["source"]["source_hash_invariant"] is True
    assert report["source"]["paper_db_sha256"] == report["source"]["paper_db_sha256_after"]
    assert db.read_bytes() == before


def test_sqlite_uri_is_explicitly_read_only(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")

    assert foundation.sqlite_readonly_uri(db).endswith("?mode=ro")


def test_integrity_check_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")

    class BadIntegrityConnection:
        def execute(self, sql: str):
            self.sql = sql
            return self

        def fetchone(self):
            return ("database disk image is malformed",)

        def close(self) -> None:
            return None

    monkeypatch.setattr(foundation, "open_sqlite_readonly", lambda _path: BadIntegrityConnection())

    report = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)

    assert report["status"] == "blocked"
    assert report["reason"] == "sqlite_integrity_check_failed"
    assert report["decision"] == "BLOCKED_SOURCE_INTEGRITY"


def test_missing_database_fails_closed(tmp_path: Path) -> None:
    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=tmp_path / "missing.sqlite",
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "paper_db_missing"


def test_closed_and_open_cohorts_are_separate(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["source"]["total_trade_rows"] == 14
    assert report["source"]["closed_trade_count"] == 12
    assert report["source"]["open_trade_count"] == 2
    assert report["financial_closeout"]["trades"] == 12


def test_closed_trade_without_close_date_blocks(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite", null_closed_date=True)

    report = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)

    assert report["reason"] == "closed_trade_missing_close_date"


def test_invalid_is_short_enum_fails_closed(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    connection = sqlite3.connect(db)
    connection.execute("UPDATE trades SET is_short = 2 WHERE id = 1")
    connection.commit()
    connection.close()

    report = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)

    assert report["status"] == "blocked"
    assert report["reason"] == "closed_trade_invalid_is_short"


def test_authoritative_pnl_profit_factor_and_expectancy(tmp_path: Path) -> None:
    report = build(tmp_path)
    metrics = report["financial_closeout"]

    assert metrics["net_pnl"] == 20.0
    assert metrics["gross_profit"] == 40.0
    assert metrics["gross_loss"] == 20.0
    assert metrics["profit_factor"] == 2.0
    assert metrics["expectancy"] == pytest.approx(20.0 / 12.0)
    assert metrics["breakeven_trades"] == 4


def test_drawdown_and_recovery_are_trade_ordered(tmp_path: Path) -> None:
    report = build(tmp_path)
    metrics = report["financial_closeout"]

    assert metrics["max_drawdown"] == 5.0
    assert metrics["recovery_factor"] == 4.0
    assert metrics["max_drawdown_pct"] is None


def test_trade_sharpe_sortino_are_explicitly_trade_based(tmp_path: Path) -> None:
    risk = build(tmp_path)["financial_closeout"]["risk_metrics"]

    assert risk["risk_metric_basis"] == "close_profit_trade_return_ratio"
    assert risk["trade_sharpe"] is not None
    assert risk["trade_sortino"] is not None
    assert risk["calmar"] is None


def test_trade_var_and_cvar_are_reported(tmp_path: Path) -> None:
    risk = build(tmp_path)["financial_closeout"]["risk_metrics"]

    assert risk["tail_risk_sign_convention"] == "positive_loss_magnitude"
    assert risk["trade_var_95"] >= 0
    assert risk["trade_var_99"] >= 0
    assert risk["trade_cvar_95"] >= risk["trade_var_95"]
    assert risk["trade_cvar_99"] >= risk["trade_var_99"]


def test_duration_percentiles_and_capital_hours(tmp_path: Path) -> None:
    metrics = build(tmp_path)["financial_closeout"]

    assert metrics["average_duration_minutes"] == 65.0
    assert metrics["median_duration_minutes"] == 65.0
    assert metrics["p25_duration_minutes"] == pytest.approx(37.5)
    assert metrics["p90_duration_minutes"] == pytest.approx(109.0)
    assert metrics["capital_hours"] > 0
    assert metrics["pnl_per_capital_hour"] is not None


def test_fees_and_funding_are_not_double_subtracted(tmp_path: Path) -> None:
    metrics = build(tmp_path)["financial_closeout"]

    assert metrics["net_pnl"] == 20.0
    assert metrics["fees_open_total"] == pytest.approx(2.4)
    assert metrics["fees_close_total"] == pytest.approx(3.6)
    assert metrics["fees_total"] == pytest.approx(6.0)
    assert metrics["funding_fees_total"] == pytest.approx(-1.2)
    assert metrics["funding_positive_revenue_total"] == 0.0
    assert metrics["funding_negative_cost_magnitude_total"] == pytest.approx(1.2)
    assert metrics["funding_net_revenue_total"] == pytest.approx(-1.2)
    assert metrics["funding_net_cost_total"] == pytest.approx(1.2)
    assert metrics["funding_sign_convention"] == "source_positive_revenue_negative_cost"
    assert metrics["fee_funding_treatment"] == "reported_separately_not_subtracted_again"


def test_funding_net_semantics_separate_revenue_and_cost(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=2, open_count=0)
    connection = sqlite3.connect(db)
    connection.execute("UPDATE trades SET funding_fees = 0.4 WHERE id = 1")
    connection.execute("UPDATE trades SET funding_fees = -0.1 WHERE id = 2")
    connection.commit()
    connection.close()

    metrics = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
    )["financial_closeout"]

    assert metrics["funding_positive_revenue_total"] == pytest.approx(0.4)
    assert metrics["funding_negative_cost_magnitude_total"] == pytest.approx(0.1)
    assert metrics["funding_net_revenue_total"] == pytest.approx(0.3)
    assert metrics["funding_net_cost_total"] == pytest.approx(-0.3)
    assert metrics["net_pnl"] == 5.0


def test_long_mfe_mae_formula(tmp_path: Path) -> None:
    source = read_authoritative_paper_source(create_paper_db(tmp_path / "paper.sqlite", closed_count=2, open_count=0))
    closed, _counts = prepare_closed_trades(source["trades"])
    long_only = closed.loc[closed["side"].eq("LONG")]

    metrics = foundation._mfe_mae_metrics(long_only)

    assert metrics["mfe_mean"] == pytest.approx(0.10)
    assert metrics["mae_mean"] == pytest.approx(-0.10)


def test_short_mfe_mae_formula(tmp_path: Path) -> None:
    source = read_authoritative_paper_source(create_paper_db(tmp_path / "paper.sqlite", closed_count=2, open_count=0))
    closed, _counts = prepare_closed_trades(source["trades"])
    short_only = closed.loc[closed["side"].eq("SHORT")]

    metrics = foundation._mfe_mae_metrics(short_only)

    assert metrics["mfe_mean"] == pytest.approx(0.10)
    assert metrics["mae_mean"] == pytest.approx(-0.10)


def test_price_give_back_uses_realized_price_move_not_pnl_ratio(tmp_path: Path) -> None:
    source = read_authoritative_paper_source(
        create_paper_db(tmp_path / "paper.sqlite", closed_count=2, open_count=0)
    )
    closed, _counts = prepare_closed_trades(source["trades"])

    metrics = foundation._mfe_mae_metrics(closed)

    assert metrics["price_give_back_basis"] == (
        "mfe_price_ratio_minus_realized_open_to_close_price_ratio"
    )
    assert metrics["price_give_back_valid_trade_count"] == 2
    assert metrics["price_give_back_mean"] == pytest.approx(0.10)


def test_pair_side_segmentations(tmp_path: Path) -> None:
    segments = build(tmp_path)["financial_closeout"]["segmentations"]

    assert len(segments["pair"]) == 2
    assert {row["side"] for row in segments["side"]} == {"LONG", "SHORT"}
    assert len(segments["pair_side"]) == 2


def test_month_week_weekday_and_hour_segmentations(tmp_path: Path) -> None:
    segments = build(tmp_path)["financial_closeout"]["segmentations"]

    assert segments["month"]
    assert segments["iso_week"]
    assert segments["weekday_utc"]
    assert segments["hour_utc"][0]["hour_utc"] == "0"


def test_exit_reason_and_epoch_segmentations(tmp_path: Path) -> None:
    segments = build(tmp_path)["financial_closeout"]["segmentations"]

    assert {row["exit_reason"] for row in segments["exit_reason"]} == {"exit_signal", "roi"}
    assert {row["epoch"] for row in segments["epoch"]} == {"P1"}
    assert segments["temporal_cohort_basis"] == "open_date_utc"


def test_order_execution_diagnostics_do_not_infer_pnl(tmp_path: Path) -> None:
    orders = build(tmp_path)["financial_closeout"]["orders"]

    assert orders["orders_total_closed_cohort"] == 24
    assert orders["entry_order_count"] == 12
    assert orders["exit_order_count"] == 12
    assert orders["execution_pnl_inferred_from_orders"] is False


def test_score_source_missing_does_not_block_financial_closeout(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["status"] == "ok"
    assert report["score_status"] == "SOURCE_MISSING"
    assert report["decision"] == "SCORE_RECALIBRATION_REQUIRED"


def test_score_partial_coverage_is_measured(tmp_path: Path) -> None:
    score = write_score_source(tmp_path / "scores.csv", 7)
    report = build(tmp_path, score_source=score)

    assert report["score_calibration"]["matched_closed_trades"] == 7
    assert report["score_calibration"]["closed_trades_without_score"] == 5
    assert report["score_status"] == "PARTIALLY_CALIBRATED"


def test_probability_metrics_reject_out_of_range_values(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    source = read_authoritative_paper_source(db)
    closed, _counts = prepare_closed_trades(source["trades"])
    score = write_score_source(tmp_path / "scores.csv", 12, out_of_range=True)

    report = build_score_calibration(score, closed, source["orders"])

    assert report["score_metrics"]["financial_win_probability"]["probability_metrics_status"] == (
        "invalid_financial_probability_range"
    )
    assert report["score_status"] == "UNCALIBRATED"


def test_brier_and_ece_are_computed_for_probability_score(tmp_path: Path) -> None:
    score = write_score_source(tmp_path / "scores.csv", 12)
    report = build(tmp_path, score_source=score)
    probability = report["score_calibration"]["score_metrics"]["financial_win_probability"][
        "probability_metrics"
    ]

    assert probability["brier_score"] is not None
    assert probability["expected_calibration_error"] is not None
    assert report["score_status"] == "CALIBRATED"


def test_prob_up_and_signal_confidence_are_not_financial_probabilities(tmp_path: Path) -> None:
    score = write_score_source(tmp_path / "scores.csv", 12)
    metrics = build(tmp_path, score_source=score)["score_calibration"]["score_metrics"]

    for column in ("prob_up", "signal_confidence"):
        assert metrics[column]["is_financial_win_probability"] is False
        assert metrics[column]["probability_metrics"] is None
        assert metrics[column]["probability_metrics_status"] == (
            "not_financial_win_probability_semantics"
        )


def test_high_coverage_with_poor_calibration_is_not_calibrated(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    rows = [
        {
            "trade_id": trade_id,
            "lineage_timestamp_utc": lineage_before_entry(trade_id),
            "financial_win_probability": 0.01 if trade_id % 3 == 1 else 0.99,
        }
        for trade_id in range(1, 13)
    ]
    pd.DataFrame(rows).to_csv(score, index=False)

    calibration = build(tmp_path, score_source=score)["score_calibration"]

    assert calibration["financial_probability_coverage_rate"] == 1.0
    assert calibration["score_status"] == "UNCALIBRATED"
    assert calibration["calibration_gate"]["passed"] is False
    assert calibration["calibration_gate"]["checks"] == {
        "brier_score_within_limit": False,
        "expected_calibration_error_within_limit": False,
        "roc_auc_meets_minimum": False,
        "expectancy_monotonic_non_decreasing": False,
        "multiple_distinct_score_buckets": True,
    }


def test_calibration_gate_requires_ece_brier_auc_and_monotonicity(tmp_path: Path) -> None:
    score = write_score_source(tmp_path / "scores.csv", 12)

    gate = build(tmp_path, score_source=score)["score_calibration"]["calibration_gate"]

    assert gate["passed"] is True
    assert all(gate["checks"].values())
    assert set(gate["checks"]) == {
        "brier_score_within_limit",
        "expected_calibration_error_within_limit",
        "roc_auc_meets_minimum",
        "expectancy_monotonic_non_decreasing",
        "multiple_distinct_score_buckets",
    }


def test_score_deciles_and_monotonicity_are_explicit(tmp_path: Path) -> None:
    score = write_score_source(tmp_path / "scores.csv", 12)
    metric = build(tmp_path, score_source=score)["score_calibration"]["score_metrics"]["qlib_score"]

    assert len(metric["deciles"]) == 10
    assert isinstance(metric["expectancy_monotonic_non_decreasing"], bool)
    assert metric["probability_metrics_status"] == "not_financial_win_probability_semantics"


def test_constant_or_tied_score_does_not_create_artificial_deciles(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    pd.DataFrame(
        [
            {
                "trade_id": trade_id,
                "lineage_timestamp_utc": lineage_before_entry(trade_id),
                "qlib_score": 0.5,
            }
            for trade_id in range(1, 13)
        ]
    ).to_csv(score, index=False)

    metric = build(tmp_path, score_source=score)["score_calibration"]["score_metrics"][
        "qlib_score"
    ]

    assert metric["unique_score_count"] == 1
    assert metric["decile_count"] == 1
    assert len(metric["deciles"]) == 1
    assert metric["deciles"][0]["count"] == 12
    assert metric["expectancy_monotonic_non_decreasing"] is False


def test_duplicate_score_trade_identity_is_not_fuzzy_matched(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    pd.DataFrame(
        [
            {
                "trade_id": 1,
                "lineage_timestamp_utc": lineage_before_entry(1),
                "prob_up": 0.2,
            },
            {
                "trade_id": 1,
                "lineage_timestamp_utc": lineage_before_entry(1),
                "prob_up": 0.8,
            },
        ]
    ).to_csv(score, index=False)
    report = build(tmp_path, score_source=score)

    assert report["score_calibration"]["matched_closed_trades"] == 0
    assert report["score_calibration"]["unmatched_score_rows"] == 2


def test_future_lineage_timestamp_is_rejected(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    pd.DataFrame(
        [
            {
                "trade_id": 1,
                "lineage_timestamp_utc": "2026-05-03T00:00:00Z",
                "financial_win_probability": 0.95,
            }
        ]
    ).to_csv(score, index=False)

    calibration = build(tmp_path, score_source=score)["score_calibration"]

    assert calibration["matched_closed_trades"] == 0
    assert calibration["score_status"] == "UNCALIBRATED"
    assert calibration["point_in_time_lineage_status"] == "blocked"
    assert calibration["future_lineage_rejected_row_count"] == 1
    assert calibration["external_match_rejection_reason_counts"][
        "future_lineage_timestamp"
    ] == 1


def test_valid_point_in_time_lineage_is_accepted(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    pd.DataFrame(
        [
            {
                "trade_id": 1,
                "lineage_timestamp_utc": lineage_before_entry(1),
                "financial_win_probability": 0.95,
            }
        ]
    ).to_csv(score, index=False)

    calibration = build(tmp_path, score_source=score)["score_calibration"]

    assert calibration["matched_closed_trades"] == 1
    assert calibration["point_in_time_valid_row_count"] == 1
    assert calibration["future_lineage_rejected_row_count"] == 0
    assert calibration["point_in_time_lineage_status"] == "ok"


def test_generic_id_is_not_an_external_identity(tmp_path: Path) -> None:
    score = tmp_path / "scores.csv"
    pd.DataFrame(
        [
            {
                "id": 1,
                "lineage_timestamp_utc": lineage_before_entry(1),
                "financial_win_probability": 0.95,
            }
        ]
    ).to_csv(score, index=False)

    calibration = build(tmp_path, score_source=score)["score_calibration"]

    assert calibration["matched_closed_trades"] == 0
    assert calibration["generic_id_accepted"] is False
    assert calibration["external_match_rejection_reason_counts"][
        "explicit_external_identity_missing"
    ] == 1


def test_regime_source_missing_is_controlled(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["regime_status"] == "SOURCE_MISSING"
    assert report["regime_oos"]["walkforward_status"] == "not_run"


def test_aligned_counter_and_range_are_derived_from_point_in_time_source(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 30)
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=30, open_count=0)
    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
        regime_source=regime,
        minimum_regime_sample=1,
    )
    folds = report["regime_oos"]["folds"]

    assert folds
    assert report["regime_oos"]["regime_column"] == "entry_market_regime"
    assert report["regime_oos"]["regime_enum_status"] == "ok"
    observed = {
        state
        for fold in folds
        for state, metrics in fold["metrics"].items()
        if state != "ALL" and metrics["sample_count"] > 0
    }
    assert observed == {"ALIGNED", "COUNTER_TREND", "RANGE"}


def test_unknown_regime_enum_fails_closed(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 30)
    frame = pd.read_csv(regime)
    frame.loc[0, "entry_market_regime"] = "invented_regime"
    frame.to_csv(regime, index=False)
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=30, open_count=0)

    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
        regime_source=regime,
        minimum_regime_sample=1,
    )["regime_oos"]

    assert report["regime_enum_status"] == "blocked"
    assert report["invalid_regime_row_count"] == 1
    assert report["invalid_regime_values"] == ["invented_regime"]
    assert report["regime_status"] == "INCONCLUSIVE"
    assert report["walkforward_status"] == "blocked"


def test_walkforward_is_temporal_and_uses_certified_engine(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 30)
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=30, open_count=0)
    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
        regime_source=regime,
        minimum_regime_sample=1,
    )["regime_oos"]

    assert report["split_engine"] == "walkforward_anti_leakage_split_engine_v1"
    assert report["fold_count"] == 3
    assert all(pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["test_start"]) for fold in report["folds"])


def test_walkforward_applies_purge_and_embargo_contract(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 30)
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=30, open_count=0)
    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
        regime_source=regime,
        embargo_seconds=3_600,
        minimum_regime_sample=1,
    )["regime_oos"]

    assert report["embargo_seconds"] == 3_600
    assert all("purged_row_count" in fold and "embargoed_row_count" in fold for fold in report["folds"])


def test_walkforward_reports_no_temporal_leakage(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 30)
    db = create_paper_db(tmp_path / "paper.sqlite", closed_count=30, open_count=0)
    report = build_paper_edge_foundation_v1(
        project_root=tmp_path,
        paper_db=db,
        regime_source=regime,
        minimum_regime_sample=1,
    )["regime_oos"]

    assert report["temporal_overlap_count"] == 0
    assert report["leakage_detected"] is False
    assert report["walkforward_status"] == "ok"


def test_regime_fold_marks_insufficient_samples(tmp_path: Path) -> None:
    regime = write_regime_source(tmp_path / "regime.csv", 12)
    report = build(tmp_path, regime_source=regime, minimum_regime_sample=20)["regime_oos"]

    assert report["folds"]
    assert all(
        fold["metrics"][state]["status"] == "insufficient_sample"
        for fold in report["folds"]
        for state in ("ALIGNED", "COUNTER_TREND", "RANGE")
    )


def test_report_is_deterministic_except_generated_at(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    first = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)
    second = build_paper_edge_foundation_v1(project_root=tmp_path, paper_db=db)

    first.pop("generated_at_utc")
    second.pop("generated_at_utc")
    assert first == second


def test_default_is_no_write(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports" / "paper_edge_foundation_v1.json").exists()


def test_write_report_only_materializes_canonical_json(tmp_path: Path) -> None:
    report = build(tmp_path, write_report=True)
    output = tmp_path / "data" / "reports" / "paper_edge_foundation_v1.json"

    assert report["write_performed"] is True
    assert output.exists()
    assert [path for path in (tmp_path / "data" / "reports").rglob("*") if path.is_file()] == [output]


def test_output_outside_data_reports_is_rejected(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")

    with pytest.raises(ValueError, match="output_report_must_be_under_data_reports"):
        build_paper_edge_foundation_v1(
            project_root=tmp_path,
            paper_db=db,
            write_report=True,
            output_report=tmp_path / "outside.json",
        )


def test_safety_flags_are_fail_closed(tmp_path: Path) -> None:
    report = build(tmp_path)

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["read_only"] is True
    for field in (
        "operational_authority",
        "writes_sqlite",
        "writes_runtime",
        "writes_active_model",
        "writes_active_signals",
        "changes_risk",
        "changes_strategy",
        "sends_orders",
        "exchange_private_access",
        "live_release_allowed",
        "canary_release_allowed",
    ):
        assert report[field] is False


def test_cli_json_executes_without_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    module = load_script()

    rc = module.main(["--project-root", str(tmp_path), "--paper-db", str(db), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "ok"
    assert payload["write_performed"] is False


def test_json_is_serializable(tmp_path: Path) -> None:
    assert json.dumps(build(tmp_path), sort_keys=True)
