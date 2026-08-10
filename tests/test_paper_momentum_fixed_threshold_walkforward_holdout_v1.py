from __future__ import annotations

import ast
import math
from pathlib import Path

import pandas as pd

from smartcrypto.research.paper_momentum_fixed_threshold_walkforward_holdout.contracts import (
    ARM_CONTROL,
    ARM_RET12,
    ARM_RET12_RET1,
    HOLDOUT_INDEPENDENCE,
    RET1_THRESHOLD,
    RET12_THRESHOLD,
    SAFETY_FLAGS,
)
from smartcrypto.research.paper_momentum_fixed_threshold_walkforward_holdout.validation import (
    build_fixed_arm_masks,
    build_walkforward_folds,
    rank_development_candidates,
    validate_fixed_threshold_momentum,
)


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SOURCE = (
    ROOT
    / "smartcrypto/research/paper_momentum_fixed_threshold_walkforward_holdout/validation.py"
)


def momentum_frame(
    count: int = 120,
    *,
    profitable: bool = True,
    bad_holdout: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    holdout_start = count - max(20, math.ceil(count * 0.20))
    for index in range(count):
        ret12_selected = index % 2 == 0
        combo_selected = index % 6 in {0, 2}
        if bad_holdout and index >= holdout_start:
            pnl = -0.8 if ret12_selected else 0.1
        elif profitable:
            if combo_selected:
                pnl = 1.0
            elif ret12_selected:
                pnl = 0.5
            else:
                pnl = -0.6
        else:
            pnl = -0.8 if ret12_selected else 0.1
        rows.append(
            {
                "stable_trade_id": f"freqtrade-paper-{3000 + index}",
                "trade_id": 3000 + index,
                "symbol": "ETHUSDT" if index % 3 else "BTCUSDT",
                "side": "long" if index % 2 else "short",
                "open_time_utc": start + pd.Timedelta(minutes=index * 10),
                "close_time_utc": start + pd.Timedelta(minutes=index * 10 + 5),
                "net_pnl": pnl,
                "analysis_eligible": True,
                "financial_decomposition_status": "authoritative_reconciled",
                "accounting_reconciled": True,
                "rejection_reason": pd.NA,
                "analysis_block_reason": pd.NA,
                "entry_return_12": 0.006 if ret12_selected else -0.002,
                "entry_return_1": 0.002 if combo_selected else -0.001,
                "mfe_absolute": max(pnl, 0.2),
                "mfe_pct": 0.006 if ret12_selected else 0.001,
                "mae_absolute": -0.5,
                "mae_pct": -0.003,
                "time_to_mfe_seconds": 120.0,
                "time_to_mae_seconds": 240.0,
            }
        )
    return pd.DataFrame(rows)


def test_thresholds_are_exactly_the_two_preauthorized_values() -> None:
    assert RET12_THRESHOLD == 0.004890587971048965
    assert RET1_THRESHOLD == 0.0013730468839541765


def test_fixed_masks_are_fail_closed_for_missing_values() -> None:
    frame = pd.DataFrame(
        {
            "entry_return_12": [RET12_THRESHOLD, RET12_THRESHOLD - 1e-12, pd.NA],
            "entry_return_1": [RET1_THRESHOLD, RET1_THRESHOLD, pd.NA],
        }
    )

    masks = build_fixed_arm_masks(frame)

    assert masks[ARM_CONTROL].tolist() == [True, True, True]
    assert masks[ARM_RET12].tolist() == [True, False, False]
    assert masks[ARM_RET12_RET1].tolist() == [True, False, False]


def test_walkforward_builds_three_expanding_history_folds() -> None:
    folds = build_walkforward_folds(96)

    assert len(folds) == 3
    assert folds[0]["train_start"] == 0
    assert folds[0]["train_end_exclusive"] == folds[0]["validation_start"]
    assert folds[1]["train_end_exclusive"] > folds[0]["train_end_exclusive"]
    assert folds[2]["validation_end_exclusive"] == 96


def test_ranker_does_not_use_holdout_fields() -> None:
    common = {
        "development_decision": "FREEZE_FOR_REPLAY_HOLDOUT",
        "positive_walkforward_fold_count": 3,
        "walkforward_candidate_net_pnl": 10.0,
        "walkforward_expectancy": 0.2,
        "walkforward_profit_factor": 2.0,
        "walkforward_maximum_drawdown": 2.0,
        "positive_pnl_retention_ratio": 0.7,
    }
    candidates = [
        {
            "arm_id": "a",
            **common,
            "walkforward_total_delta_pnl": 8.0,
            "holdout_net_pnl": -1000.0,
        },
        {
            "arm_id": "b",
            **common,
            "walkforward_total_delta_pnl": 7.0,
            "holdout_net_pnl": 1000.0,
        },
    ]

    ranked = rank_development_candidates(candidates)

    assert ranked[0]["arm_id"] == "a"


def test_end_to_end_freezes_ret12_before_replay_holdout() -> None:
    frame = momentum_frame()
    original = frame.copy(deep=True)

    dataset, report = validate_fixed_threshold_momentum(frame)

    pd.testing.assert_frame_equal(frame, original)
    assert report["status"] == "ok"
    assert report["eligible_trade_count"] == 120
    assert report["development_trade_count"] == 96
    assert report["holdout_trade_count"] == 24
    assert report["frozen_champion"] is not None
    assert report["frozen_champion"]["arm_id"] == ARM_RET12
    assert report["frozen_champion"]["holdout_metrics_used_for_selection"] is False
    assert report["replay_holdout_evaluation"]["arm_id"] == ARM_RET12
    assert report["replay_holdout_evaluation"]["holdout_used_for_selection"] is False
    assert report["replay_holdout_passed"] is True
    assert report["ready_for_forward_paper_ab"] is True
    assert report["ready_for_paper_wiring"] is False
    assert set(dataset["momentum_validation_partition"]) >= {
        "development",
        "replay_holdout",
    }


def test_bad_replay_holdout_does_not_change_frozen_champion() -> None:
    _, positive_report = validate_fixed_threshold_momentum(momentum_frame())
    _, bad_report = validate_fixed_threshold_momentum(momentum_frame(bad_holdout=True))

    assert positive_report["frozen_champion"] is not None
    assert bad_report["frozen_champion"] is not None
    assert positive_report["frozen_champion"]["arm_id"] == ARM_RET12
    assert bad_report["frozen_champion"]["arm_id"] == ARM_RET12
    assert bad_report["replay_holdout_passed"] is False
    assert bad_report["ready_for_forward_paper_ab"] is False


def test_candidate_must_pass_at_least_two_walkforward_folds() -> None:
    _, report = validate_fixed_threshold_momentum(momentum_frame())

    champion = report["frozen_champion"]
    assert champion is not None
    assert champion["positive_walkforward_fold_count"] >= 2
    assert champion["walkforward_total_delta_pnl"] > 0
    assert champion["walkforward_candidate_net_pnl"] > 0
    assert champion["walkforward_expectancy"] > 0
    profit_factor = champion["walkforward_profit_factor"]
    assert profit_factor is None or profit_factor > 1.0


def test_unprofitable_fixed_filters_do_not_open_holdout() -> None:
    _, report = validate_fixed_threshold_momentum(momentum_frame(profitable=False))

    assert report["status"] == "ok"
    assert report["frozen_champion"] is None
    assert report["replay_holdout_evaluation"] is None
    assert report["replay_holdout_passed"] is False
    assert report["ready_for_forward_paper_ab"] is False


def test_historical_exposure_is_explicit_and_blocks_wiring_claim() -> None:
    assert HOLDOUT_INDEPENDENCE["isolated_inside_this_validation"] is True
    assert HOLDOUT_INDEPENDENCE[
        "walkforward_historically_unseen_during_threshold_discovery"
    ] is False
    assert HOLDOUT_INDEPENDENCE[
        "holdout_historically_unseen_during_threshold_discovery"
    ] is False

    _, report = validate_fixed_threshold_momentum(momentum_frame())

    assert report["holdout_independence"][
        "holdout_historically_unseen_during_threshold_discovery"
    ] is False
    assert report["ready_for_paper_wiring"] is False


def test_scope_contains_no_profit_protection_or_threshold_search() -> None:
    source = VALIDATION_SOURCE.read_text(encoding="utf-8")
    source_casefold = source.casefold()
    tree = ast.parse(source)

    imported_modules: list[str] = []
    imported_symbols: list[str] = []
    called_names: list[str] = []
    called_attributes: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name.casefold() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.append(node.module.casefold())
            imported_symbols.extend(alias.name.casefold() for alias in node.names)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.append(node.func.id.casefold())
            elif isinstance(node.func, ast.Attribute):
                called_attributes.append(node.func.attr.casefold())

    assert all("profit_protection" not in module for module in imported_modules)
    assert "simulate_trade_path" not in imported_symbols
    assert "simulate_candidate_frame" not in imported_symbols
    assert "validate_path_faithful_candidates" not in imported_symbols
    assert "simulate_trade_path" not in called_names
    assert "simulate_candidate_frame" not in called_names
    assert "validate_path_faithful_candidates" not in called_names
    assert "quantile" not in called_attributes
    assert "linspace" not in called_names
    assert "random" not in imported_modules
    assert ".quantile(" not in source_casefold


def test_safety_flags_forbid_operational_mutation() -> None:
    assert SAFETY_FLAGS["research_only"] is True
    assert SAFETY_FLAGS["read_only"] is True
    assert SAFETY_FLAGS["paper_only"] is True
    assert SAFETY_FLAGS["operational_authority"] is False
    assert SAFETY_FLAGS["sends_orders"] is False
    assert SAFETY_FLAGS["exchange_private_access"] is False
    assert SAFETY_FLAGS["uses_profit_protection"] is False
    assert SAFETY_FLAGS["searches_new_thresholds"] is False
    assert SAFETY_FLAGS["changes_risk"] is False
    assert SAFETY_FLAGS["changes_roi"] is False
    assert SAFETY_FLAGS["changes_stoploss"] is False
    assert SAFETY_FLAGS["writes_runtime"] is False
    assert SAFETY_FLAGS["deploy_performed"] is False
