from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

from smartcrypto.research.paper_momentum_forward_oos_observer.contracts import (
    FORWARD_FREEZE_COMMIT,
    FORWARD_START_UTC,
    FROZEN_FILTER_ID,
    MIN_FORWARD_CANDIDATE_TRADES,
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    SAFETY_FLAGS,
)
from smartcrypto.research.paper_momentum_forward_oos_observer.validation import (
    build_frozen_candidate_mask,
    observe_frozen_momentum_forward,
)


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SOURCE = (
    ROOT / "smartcrypto/research/paper_momentum_forward_oos_observer/validation.py"
)


def forward_frame(
    *,
    post_freeze_count: int = 60,
    pre_freeze_count: int = 10,
    bad_second_half: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    trade_id = 5000

    for index in range(pre_freeze_count):
        close_time = FORWARD_START_UTC - pd.Timedelta(
            minutes=pre_freeze_count - index
        )
        rows.append(
            _row(
                trade_id=trade_id,
                close_time=close_time,
                selected=True,
                pnl=10.0,
            )
        )
        trade_id += 1

    midpoint = post_freeze_count // 2
    for index in range(post_freeze_count):
        selected = index % 2 == 0
        if bad_second_half and index >= midpoint:
            pnl = 0.5 if selected else 0.6
        else:
            pnl = 1.0 if selected else -0.4
        close_time = FORWARD_START_UTC + pd.Timedelta(minutes=(index + 1) * 5)
        rows.append(
            _row(
                trade_id=trade_id,
                close_time=close_time,
                selected=selected,
                pnl=pnl,
            )
        )
        trade_id += 1

    return pd.DataFrame(rows)


def _row(
    *,
    trade_id: int,
    close_time: pd.Timestamp,
    selected: bool,
    pnl: float,
) -> dict[str, object]:
    return {
        "stable_trade_id": f"freqtrade-paper-{trade_id}",
        "trade_id": trade_id,
        "symbol": "ETHUSDT" if trade_id % 3 else "BTCUSDT",
        "side": "long" if trade_id % 2 else "short",
        "open_time_utc": close_time - pd.Timedelta(minutes=5),
        "close_time_utc": close_time,
        "net_pnl": pnl,
        "analysis_eligible": True,
        "financial_decomposition_status": "authoritative_reconciled",
        "accounting_reconciled": True,
        "rejection_reason": pd.NA,
        "analysis_block_reason": pd.NA,
        "entry_return_12": 0.006 if selected else -0.002,
        "entry_return_1": 0.002 if selected else -0.001,
        "mfe_absolute": max(pnl, 0.2),
        "mfe_pct": 0.006 if selected else 0.001,
        "mae_absolute": -0.5,
        "mae_pct": -0.003,
        "time_to_mfe_seconds": 120.0,
        "time_to_mae_seconds": 240.0,
    }


def test_freeze_contract_is_exact_and_thresholds_are_not_changed() -> None:
    assert FORWARD_FREEZE_COMMIT == "ed4efef093120786bd2b417ecb8d068373879679"
    assert FORWARD_START_UTC == pd.Timestamp("2026-08-10T00:51:10Z")
    assert FROZEN_FILTER_ID == "momentum_ret12_ret1"
    assert RET12_THRESHOLD == 0.004890587971048965
    assert RET1_THRESHOLD == 0.0013730468839541765
    assert MIN_FORWARD_CANDIDATE_TRADES == 30


def test_cutoff_is_strictly_greater_than_close_time() -> None:
    frame = pd.DataFrame(
        [
            _row(
                trade_id=6000,
                close_time=FORWARD_START_UTC,
                selected=True,
                pnl=1.0,
            ),
            _row(
                trade_id=6001,
                close_time=FORWARD_START_UTC + pd.Timedelta(seconds=1),
                selected=True,
                pnl=1.0,
            ),
        ]
    )

    _, report = observe_frozen_momentum_forward(frame)

    assert report["forward_control_trade_count"] == 1
    assert report["forward_candidate_trade_count"] == 1
    assert report["cutoff_operator"] == "close_time_utc_gt"


def test_candidate_mask_is_exact_and_missing_features_fail_closed() -> None:
    frame = pd.DataFrame(
        {
            "entry_return_12": [RET12_THRESHOLD, RET12_THRESHOLD, pd.NA],
            "entry_return_1": [RET1_THRESHOLD, RET1_THRESHOLD - 1e-12, pd.NA],
        }
    )

    mask = build_frozen_candidate_mask(frame)

    assert mask.tolist() == [True, False, False]


def test_observer_does_not_mutate_source_frame() -> None:
    frame = forward_frame(post_freeze_count=12)
    original = frame.copy(deep=True)

    observe_frozen_momentum_forward(frame)

    pd.testing.assert_frame_equal(frame, original)


def test_observer_collects_without_claiming_readiness_before_30_candidates() -> None:
    _, report = observe_frozen_momentum_forward(
        forward_frame(post_freeze_count=20, pre_freeze_count=2)
    )

    assert report["forward_control_trade_count"] == 20
    assert report["forward_candidate_trade_count"] == 10
    assert report["forward_evidence_ready"] is False
    assert report["forward_gate_passed"] is False
    assert report["eligible_for_future_paper_wiring_review"] is False
    assert report["ready_for_paper_wiring"] is False
    assert report["reason"] == "forward_oos_collecting_insufficient_candidate_evidence"


def test_exact_30_candidate_trades_can_pass_forward_gate() -> None:
    _, report = observe_frozen_momentum_forward(forward_frame())

    assert report["forward_control_trade_count"] == 60
    assert report["forward_candidate_trade_count"] == 30
    assert report["feature_coverage_ratio"] == 1.0
    assert report["forward_evidence_ready"] is True
    assert report["candidate_metrics"]["net_pnl"] > 0
    assert report["candidate_metrics"]["expectancy"] > 0
    assert report["delta_pnl"] > 0
    assert report["candidate_metrics"]["maximum_drawdown"] < report[
        "control_metrics"
    ]["maximum_drawdown"]
    assert report["first_half"]["delta_pnl"] > 0
    assert report["second_half"]["delta_pnl"] > 0
    assert report["forward_gate_passed"] is True
    assert report["eligible_for_future_paper_wiring_review"] is True
    assert report["ready_for_paper_wiring"] is False


def test_negative_second_half_delta_blocks_forward_gate() -> None:
    _, report = observe_frozen_momentum_forward(
        forward_frame(bad_second_half=True)
    )

    assert report["forward_candidate_trade_count"] == 30
    assert report["first_half"]["delta_pnl"] > 0
    assert report["second_half"]["delta_pnl"] < 0
    assert report["second_half"]["segment_passed"] is False
    assert report["forward_gate_passed"] is False


def test_missing_forward_feature_coverage_blocks_evidence_readiness() -> None:
    frame = forward_frame()
    post_freeze_index = frame.index[-1]
    frame.loc[post_freeze_index, "entry_return_1"] = pd.NA

    _, report = observe_frozen_momentum_forward(frame)

    assert report["forward_candidate_trade_count"] == 30
    assert report["feature_coverage_ratio"] < 1.0
    assert report["forward_evidence_ready"] is False
    assert report["forward_gate_passed"] is False


def test_pre_freeze_trades_never_affect_forward_metrics() -> None:
    _, report_a = observe_frozen_momentum_forward(
        forward_frame(pre_freeze_count=0, post_freeze_count=20)
    )
    _, report_b = observe_frozen_momentum_forward(
        forward_frame(pre_freeze_count=25, post_freeze_count=20)
    )

    assert report_a["control_metrics"] == report_b["control_metrics"]
    assert report_a["candidate_metrics"] == report_b["candidate_metrics"]
    assert report_a["delta_pnl"] == report_b["delta_pnl"]


def test_diagnostic_subgroups_do_not_change_candidate_count() -> None:
    _, report = observe_frozen_momentum_forward(forward_frame())

    assert report["forward_candidate_trade_count"] == 30
    assert "symbol" in report["diagnostics_only"]
    assert "side" in report["diagnostics_only"]
    assert report["validation_contract"][
        "diagnostic_subgroups_can_change_filter"
    ] is False


def test_scope_has_no_profit_protection_or_threshold_search_calls() -> None:
    tree = ast.parse(VALIDATION_SOURCE.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    called_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.append(node.func.attr)

    assert not any("profit_protection" in module for module in imported_modules)
    assert "quantile" not in called_names
    assert "linspace" not in called_names
    assert "simulate_trade_path" not in called_names


def test_safety_flags_forbid_operational_mutation() -> None:
    assert SAFETY_FLAGS["research_only"] is True
    assert SAFETY_FLAGS["read_only"] is True
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["operational_authority"] is False
    assert SAFETY_FLAGS["sends_orders"] is False
    assert SAFETY_FLAGS["exchange_private_access"] is False
    assert SAFETY_FLAGS["blocks_entries"] is False
    assert SAFETY_FLAGS["uses_profit_protection"] is False
    assert SAFETY_FLAGS["searches_new_thresholds"] is False
    assert SAFETY_FLAGS["changes_risk"] is False
    assert SAFETY_FLAGS["changes_roi"] is False
    assert SAFETY_FLAGS["changes_stoploss"] is False
    assert SAFETY_FLAGS["writes_runtime"] is False
    assert SAFETY_FLAGS["deploy_performed"] is False
