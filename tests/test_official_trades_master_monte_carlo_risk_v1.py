from __future__ import annotations

from pathlib import Path

import numpy as np

from scripts.run_official_trades_master_monte_carlo_risk_v1 import (
    MANIFEST_CSV,
    REPORT_JSON,
    REPORT_MD,
    RUIN_CSV,
    SCENARIOS_CSV,
    _blocked,
    build_parser,
    persist_wq5_reports,
    render_markdown,
)
from smartcrypto.research.trades_master_official.monte_carlo import (
    _acceptance,
    _iid_sampler,
    _run_scenario,
    _tail_probabilities,
    moving_block_sample,
    per_path_seed,
    report_content_sha256,
)
from smartcrypto.research.trades_master_official.risk_of_ruin import (
    calculate_path_metrics,
    historical_max_drawdown,
    primary_ruin_probability,
    summarize_path_metrics,
)


def _scenario(
    *,
    p_positive: float = 1.0,
    p05: float = 10.0,
    p50: float = 20.0,
    ruin_2x: float = 0.0,
) -> dict[str, object]:
    return {
        "simulation_count": 10000,
        "terminal_p01": p05 - 10.0,
        "terminal_p05": p05,
        "terminal_p50": p50,
        "terminal_p95": p50 + 10.0,
        "terminal_p99": p50 + 20.0,
        "p_final_net_pnl_gt_zero": (p_positive),
        "max_drawdown_p50": 10.0,
        "max_drawdown_p90": 20.0,
        "max_drawdown_p95": 30.0,
        "max_drawdown_p99": 40.0,
        "terminal_loss_cvar_95": 5.0,
        "terminal_loss_cvar_99": 7.0,
        "losing_streak_p95": 4.0,
        "time_under_water_p95_trade_steps": 12.0,
        "risk_of_ruin": {
            "1": 0.01,
            "1.5": 0.005,
            "2": ruin_2x,
            "3": 0.0,
            "5": 0.0,
        },
    }


def _payload() -> dict[str, object]:
    order = [
        "baseline_shuffle",
        "baseline_iid",
        "baseline_block_b05",
        "baseline_block_b20",
        "baseline_block_b60",
        "regime_conditional_iid",
        "cost_block20_m00",
        "cost_block20_m05",
        "cost_block20_m10",
        "cost_block20_m20",
        "cost_block20_m40",
        "tail_iid_w10",
        "tail_iid_w20",
        "tail_iid_w30",
    ]

    scenarios = {scenario_id: _scenario() for scenario_id in order}

    scenarios["cost_block20_m10"] = _scenario(
        p_positive=0.0,
        p05=-59000.0,
        p50=-53000.0,
        ruin_2x=1.0,
    )

    scenarios["tail_iid_w20"] = _scenario(
        p_positive=0.8959,
        p05=-156.0,
        p50=528.0,
        ruin_2x=0.387,
    )

    return {
        "schema_version": ("official_trades_master_monte_carlo_risk_v1"),
        "status": "ok",
        "engineering_status": "PASS",
        "wq5_status": ("RISK_STRESS_READY"),
        "decision": ("WQ5_RISK_STRESS_EVALUATED"),
        "quant_classification": ("DISCARD_OR_RECALIBRATE_RESEARCH_ONLY"),
        "primary_acceptance_pass": False,
        "master_sha256": "a" * 64,
        "method_v3_hash": "b" * 64,
        "primary_population": {
            "rows": 3066,
            "historical_net_pnl": (3218.250703),
            "historical_max_drawdown": (68.467788),
            "notional_sum_usdt": (62775277.06201814),
            "notional_mean_usdt": (20474.65005284349),
            "incremental_stress_bps": 9.0,
            "incremental_cost_total_m10_usdt": (56497.74935581632),
            "incremental_cost_mean_m10_usdt": (18.42718504755914),
            "composite_regime_count": 9,
        },
        "scenario_order": order,
        "scenarios": scenarios,
        "primary_gates": {
            "baseline_iid_positive_probability": True,
            "baseline_iid_p05_positive": True,
            "baseline_block20_positive_probability": True,
            "baseline_block20_p05_positive": True,
            "cost_block20_m10_median_positive": False,
            "tail_iid_w20_median_positive": True,
            "conditional_ruin_2x_lte_005": True,
        },
        "primary_ruin_2x": 0.0,
        "all_primary_gates_pass": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "changes_risk": False,
        "changes_model": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "writes_master": False,
        "writes_sqlite": False,
    }


def test_per_path_seed_is_frozen() -> None:
    assert (
        per_path_seed(
            "baseline_iid",
            0,
        )
        == 6429414468344520424
    )

    assert (
        per_path_seed(
            "baseline_iid",
            1,
        )
        == 13105527181071660794
    )


def test_non_circular_moving_block_known_seed() -> None:
    values = np.asarray(
        [
            1.0,
            2.0,
            3.0,
            4.0,
            5.0,
            6.0,
        ],
        dtype=np.float64,
    )

    rng = np.random.Generator(np.random.PCG64(123))

    sample = moving_block_sample(
        values,
        block_size=3,
        rng=rng,
    )

    assert sample.tolist() == [
        1.0,
        2.0,
        3.0,
        3.0,
        4.0,
        5.0,
    ]


def test_tail_weight_targets_worst_ten_percent() -> None:
    pnl = np.asarray(
        [
            -10.0,
            -5.0,
            1.0,
            2.0,
            3.0,
            4.0,
        ],
        dtype=np.float64,
    )

    sequence = np.asarray(
        [
            1,
            2,
            3,
            4,
            5,
            6,
        ],
        dtype=np.int64,
    )

    probability = _tail_probabilities(
        pnl,
        sequence,
        multiplier=2.0,
    )

    assert np.isclose(
        probability.sum(),
        1.0,
    )

    assert np.isclose(
        probability[0],
        2.0 / 7.0,
    )

    assert np.allclose(
        probability[1:],
        np.full(
            5,
            1.0 / 7.0,
        ),
    )


def test_small_scenario_is_reproducible() -> None:
    pnl = np.asarray(
        [
            1.0,
            2.0,
            -1.0,
            3.0,
            -0.5,
            2.5,
        ],
        dtype=np.float64,
    )

    first = _run_scenario(
        scenario_id="unit_iid",
        horizon=6,
        sampler=_iid_sampler(pnl),
        buffer_anchor=3.0,
        simulation_count=64,
        batch_size=16,
    )

    second = _run_scenario(
        scenario_id="unit_iid",
        horizon=6,
        sampler=_iid_sampler(pnl),
        buffer_anchor=3.0,
        simulation_count=64,
        batch_size=16,
    )

    assert first == second

    assert first["simulation_count"] == 64


def test_historical_drawdown_uses_virtual_zero() -> None:
    pnl = np.asarray(
        [
            10.0,
            -4.0,
            -3.0,
            5.0,
            -20.0,
            30.0,
        ],
        dtype=np.float64,
    )

    assert historical_max_drawdown(pnl) == 22.0


def test_path_metrics_ruin_grid() -> None:
    paths = np.asarray(
        [
            [
                10.0,
                -4.0,
                -3.0,
                5.0,
            ],
            [
                -5.0,
                -5.0,
                20.0,
                1.0,
            ],
        ],
        dtype=np.float64,
    )

    summary = summarize_path_metrics(
        calculate_path_metrics(
            paths,
            buffer_anchor=10.0,
        )
    )

    assert summary["simulation_count"] == 2

    assert summary["risk_of_ruin"]["1"] == 0.5

    assert summary["risk_of_ruin"]["2"] == 0.0


def test_primary_ruin_uses_worst_primary_method() -> None:
    assert (
        primary_ruin_probability(
            {
                "baseline_iid": {
                    "risk_of_ruin": {
                        "2": 0.01,
                    },
                },
                "baseline_block_b20": {
                    "risk_of_ruin": {
                        "2": 0.03,
                    },
                },
            }
        )
        == 0.03
    )


def test_acceptance_fails_frozen_cost_gate() -> None:
    scenarios = {
        "baseline_iid": (_scenario()),
        "baseline_block_b20": (_scenario()),
        "cost_block20_m10": (
            _scenario(
                p_positive=0.0,
                p05=-59000.0,
                p50=-53000.0,
                ruin_2x=1.0,
            )
        ),
        "tail_iid_w20": (
            _scenario(
                p_positive=0.8959,
                p05=-156.0,
                p50=528.0,
                ruin_2x=0.387,
            )
        ),
    }

    (
        gates,
        ruin_2x,
        passed,
        classification,
    ) = _acceptance(scenarios)

    assert gates["cost_block20_m10_median_positive"] is False

    assert passed is False

    assert classification == ("DISCARD_OR_RECALIBRATE_RESEARCH_ONLY")

    assert ruin_2x == 0.0


def test_report_hash_ignores_transport_fields() -> None:
    payload = _payload()

    first = report_content_sha256(payload)

    second = report_content_sha256(
        {
            **payload,
            "report_content_sha256": "ignored",
            "report_paths": {
                "json": "ignored",
            },
            "write_requested": True,
            "write_performed": True,
        }
    )

    assert first == second


def test_cli_parser_requires_explicit_sources() -> None:
    args = build_parser().parse_args(
        [
            "--master",
            "master.xlsx",
            "--method-freeze-v2",
            "v2.json",
            "--method-freeze-v3",
            "v3.json",
            "--btc-existing",
            "btc-existing.parquet",
            "--eth-existing",
            "eth-existing.parquet",
            "--btc-backfill",
            "btc-backfill.parquet",
            "--eth-backfill",
            "eth-backfill.parquet",
        ]
    )

    assert args.master == "master.xlsx"

    assert args.write_report is False


def test_blocked_payload_preserves_safety() -> None:
    blocked = _blocked(
        reason="test",
        details={
            "x": 1,
        },
        master_path=("master.xlsx"),
    )

    assert blocked["status"] == "blocked"

    assert blocked["operational_authority"] is False

    assert blocked["sends_orders"] is False

    assert blocked["changes_risk"] is False


def test_markdown_exposes_population_contract() -> None:
    rendered = render_markdown(_payload())

    assert "WQ2_EXPANDING_OOS_3066" in rendered

    assert "FULL_MASTER_3991" in rendered


def test_persistence_is_deterministic(
    tmp_path: Path,
) -> None:
    payload = _payload()

    first = tmp_path / "first"

    second = tmp_path / "second"

    persist_wq5_reports(
        payload,
        output_dir=first,
    )

    persist_wq5_reports(
        payload,
        output_dir=second,
    )

    expected = {
        REPORT_JSON,
        REPORT_MD,
        SCENARIOS_CSV,
        RUIN_CSV,
        MANIFEST_CSV,
    }

    assert {path.name for path in first.iterdir()} == expected

    assert {path.name for path in second.iterdir()} == expected

    for name in expected:
        assert (first / name).read_bytes() == (second / name).read_bytes()
