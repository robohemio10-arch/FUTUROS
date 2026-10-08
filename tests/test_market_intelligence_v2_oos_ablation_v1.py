from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

from smartcrypto.research.aibot_parity import market_intelligence_v2_oos_ablation as v2
from smartcrypto.research.market_intelligence.contracts import MarketEvent


class _ScoreModel:
    def fit(self, x: np.ndarray, y: np.ndarray) -> _ScoreModel:
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return x[:, 0] + np.sum(x[:, 1:], axis=1) * 0.001


def _factory() -> _ScoreModel:
    return _ScoreModel()


def _input() -> tuple[pd.DataFrame, list[MarketEvent], list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    events: list[MarketEvent] = []
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    for index in range(100):
        opened = start + pd.Timedelta(hours=index * 2)
        observed = opened - pd.Timedelta(seconds=1)
        symbol = "BTCUSDT" if index % 2 == 0 else "ETHUSDT"
        rows.append(
            {
                "trade_sequence": index,
                "symbol": symbol,
                "side": "long" if index % 2 == 0 else "short",
                "open_time_utc": opened,
                "close_time_utc": opened + pd.Timedelta(minutes=10),
                "feature_cutoff_utc": observed,
                "feature_side_long": float(index % 7),
                "label_economic_net_pnl": float(2 if index % 3 else -3),
                "capital_proxy_usdt": 100.0,
                "capital_hours": 100.0 / 6.0,
            }
        )
        for kind, payload in (
            ("agg_trade", {"price": 100.0, "quantity": 1.0, "buyer_maker": index % 2 == 0}),
            (
                "mark_price",
                {
                    "mark_price": 100.0 + index / 10,
                    "index_price": 100.0,
                    "funding_rate": 0.0001 * (index % 3 + 1),
                    "funding_rate_kind": "predicted",
                },
            ),
        ):
            events.append(
                MarketEvent.model_validate(
                    {
                        "event_id": f"event-{index}-{kind}",
                        "source_id": "binance_usdm_futures_public",
                        "exchange": "binance",
                        "symbol": symbol,
                        "event_type": kind,
                        "event_time_utc": observed.isoformat(),
                        "received_at_utc": observed.isoformat(),
                        "available_at_utc": observed.isoformat(),
                        "source_hash": "a" * 64,
                        "payload": payload,
                    }
                )
            )
    splits: list[dict[str, object]] = []
    for number, train_end, val_end, test_end in (
        (1, 30, 50, 60),
        (2, 50, 70, 80),
        (3, 70, 90, 100),
    ):
        splits.append(
            {
                "split_id": f"fold-{number}",
                "_train_indices": list(range(train_end)),
                "_validation_indices": list(range(train_end, val_end)),
                "_test_indices": list(range(val_end, test_end)),
            }
        )
    return pd.DataFrame.from_records(rows), events, splits


def test_all_eight_variants_share_frozen_oos_and_pit_contract() -> None:
    dataset, events, splits = _input()
    enriched, coverage = v2.align_pit_features(dataset, events)
    assert all(item["feature_coverage"] == 1.0 for item in coverage.values())

    report = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    assert report["anti_leakage_status"] == "PASS"
    assert report["point_in_time_contract"] == "PASS"
    assert report["oos_universe_count"] == 30
    assert set(report["variants"]) == set(v2.VARIANTS) | {"NO_BR10_CONTROL", "BR10_CURRENT"}
    assert all(
        fold["oos_universe_count"] == 10
        for variant in report["variants"].values()
        for fold in variant["folds"]
    )
    assert all(
        variant["economic_candidate"] is False
        or variant["fold_positive_count_vs_no_br10_control"] >= 2
        for variant in report["variants"].values()
    )


def test_future_available_event_cannot_be_used_at_open() -> None:
    dataset, events, _ = _input()
    moved = events[0].model_copy(
        update={
            "available_at_utc": dataset.loc[0, "open_time_utc"].to_pydatetime()
            + pd.Timedelta(seconds=1)
        }
    )
    events[0] = moved
    _, coverage = v2.align_pit_features(dataset, events)
    assert coverage["FLOW"]["available_count"] == len(dataset) - 1
    assert coverage["FLOW"]["statuses"]["UNAVAILABLE"] == 1


def test_stale_mark_blocks_basis_and_funding() -> None:
    dataset, events, _ = _input()
    events = [event for event in events if event.event_id != "event-0-mark_price"]
    _, coverage = v2.align_pit_features(dataset, events)
    assert coverage["BASIS"]["feature_coverage"] < 1.0
    assert coverage["FUNDING"]["feature_coverage"] < 1.0


def test_oos_label_change_cannot_change_calibration_or_selection_count() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    initial = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    changed = enriched.copy()
    changed.loc[90:99, "label_economic_net_pnl"] *= -10.0
    later = v2._evaluate_oos_variants(
        dataset=changed,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    for name in v2.VARIANTS:
        first = initial["variants"][name]
        second = later["variants"][name]
        assert first["trade_count"] == second["trade_count"]
        assert first["selection_identity_sha256"] == second["selection_identity_sha256"]
        assert [fold["threshold"] for fold in first["folds"]] == [
            fold["threshold"] for fold in second["folds"]
        ]


def test_missing_pit_source_blocks_before_model(monkeypatch: pytest.MonkeyPatch) -> None:
    dataset, _, splits = _input()
    monkeypatch.setattr(
        v2,
        "_load_bundle",
        lambda **_: (dataset, ("feature_side_long",), splits, {"dataset_hash": "test"}),
    )
    report = v2.build_market_intelligence_v2_oos_ablation_v1(
        project_root=".", market_events_path=None, model_factory=_factory
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "pit_event_source_required"
    assert report["coverage"]["FLOW"]["available_count"] == 0
    assert report["write_performed"] is False


def test_invalid_source_and_duplicate_identity_fail_closed(tmp_path: Path) -> None:
    _, events, _ = _input()
    source = tmp_path / "events.jsonl"
    source.write_text(
        json.dumps(events[0].model_dump(mode="json"))
        + "\n"
        + json.dumps(events[0].model_dump(mode="json"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(v2.MarketIntelligenceV2Error, match="pit_event_identity_collision"):
        v2._load_events(source)
    source.write_text("{invalid\n", encoding="utf-8")
    with pytest.raises(v2.MarketIntelligenceV2Error, match="pit_event_source_invalid"):
        v2._load_events(source)
    source.write_text("[]\n", encoding="utf-8")
    with pytest.raises(v2.MarketIntelligenceV2Error, match="record_not_object"):
        v2._load_events(source)


def test_source_hash_drift_blocks_before_alignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, events, splits = _input()
    source = tmp_path / "events.jsonl"
    source.write_text(json.dumps(events[0].model_dump(mode="json")) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        v2,
        "_load_bundle",
        lambda **_: (dataset, ("feature_side_long",), splits, {"dataset_hash": "test"}),
    )
    report = v2.build_market_intelligence_v2_oos_ablation_v1(
        project_root=tmp_path,
        market_events_path=source,
        market_events_sha256="b" * 64,
        model_factory=_factory,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "pit_event_source_sha256_mismatch"
    assert report["anti_leakage_status"] == "BLOCKED"


def test_outcome_feature_cannot_enter_control() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    with pytest.raises(
        v2.MarketIntelligenceV2Error, match="baseline_feature_outside_frozen_contract"
    ):
        v2._evaluate_oos_variants(
            dataset=enriched,
            baseline_features=("label_economic_net_pnl",),
            splits=splits,
            model_factory=_factory,
        )


def test_pinned_complete_pit_source_runs_all_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, events, splits = _input()
    source = tmp_path / "events.jsonl"
    source.write_text(
        "".join(json.dumps(event.model_dump(mode="json")) + "\n" for event in events),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        v2,
        "_load_bundle",
        lambda **_: (dataset, ("feature_side_long",), splits, {"dataset_hash": "test"}),
    )
    report = v2.build_market_intelligence_v2_oos_ablation_v1(
        project_root=tmp_path,
        market_events_path=source,
        market_events_sha256=v2._source_hash(source),
        model_factory=_factory,
    )
    assert report["status"] == "ok"
    assert report["write_performed"] is False
    assert set(report["variants"]) == set(v2.VARIANTS) | {"NO_BR10_CONTROL", "BR10_CURRENT"}
    assert report["coverage"]["FLOW"]["feature_coverage"] == 1.0
    assert report["variants"]["FLOW_ONLY"]["stale_or_unavailable_count"] == 0


def test_basis_and_funding_variants_do_not_require_flow() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(
        dataset, [event for event in events if event.event_type == "mark_price"]
    )
    report = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    for name in ("BASIS_ONLY", "FUNDING_ONLY", "BASIS_FUNDING"):
        assert report["variants"][name]["status"] == "ok"
        assert report["variants"][name]["same_candidate_subset_as_control"] is True
    for name in ("FLOW_ONLY", "FLOW_BASIS", "FLOW_FUNDING", "FLOW_BASIS_FUNDING"):
        assert report["variants"][name]["status"] == "blocked"
        assert report["variants"][name]["net_pnl_usdt"] is None


def test_partial_source_coverage_uses_exact_variant_matched_control() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    enriched.loc[[50, 72, 95], v2.FEATURES["BASIS"]] = float("nan")
    report = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    basis = report["variants"]["BASIS_ONLY"]
    assert basis["oos_universe_count"] == 27
    assert basis["matched_control"]["oos_universe_count"] == 27
    assert basis["cohort_identity_sha256"] == basis["matched_control"]["cohort_identity_sha256"]
    assert all(
        fold["cohort_identity_sha256"] == control["cohort_identity_sha256"]
        for fold, control in zip(basis["folds"], basis["matched_control"]["folds"], strict=True)
    )
    assert report["variants"]["FUNDING_ONLY"]["oos_universe_count"] == 30
    assert basis["no_br10_comparison_reason"] == "no_br10_cohort_identity_mismatch"
    assert basis["delta_net_pnl_vs_no_br10_control"] is None
    assert basis["economic_candidate"] is False


def test_missing_train_coverage_blocks_variant_not_other_sources() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    enriched.loc[:29, v2.FEATURES["BASIS"]] = float("nan")
    report = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    assert report["variants"]["BASIS_ONLY"]["reason"] == "covered_fold_partition_empty"
    assert report["variants"]["FUNDING_ONLY"]["status"] == "ok"


def test_historical_settled_funding_source_runs_without_basis_or_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smartcrypto.research.aibot_parity import market_intelligence_pit_source_foundation as pit

    dataset, _, splits = _input()
    observations = []
    for row in dataset.itertuples(index=False):
        event = row.open_time_utc.to_pydatetime() - pd.Timedelta(minutes=6)
        observations.append(
            pit.funding_observations(
                row.symbol,
                [
                    ["calc_time", "funding_interval_hours", "last_funding_rate"],
                    [str(int(event.timestamp() * 1000)), "8", "0.0001"],
                ],
                f"futures/um/monthly/fundingRate/{row.symbol}/{row.symbol}-fundingRate-2026-01.zip",
                "a" * 64,
            )[0]
        )
    source = tmp_path / "observations.jsonl"
    source.write_bytes(
        b"".join(pit.encode(record.model_dump(mode="json")) + b"\n" for record in observations)
    )
    monkeypatch.setattr(
        v2,
        "_load_bundle",
        lambda **_: (dataset, ("feature_side_long",), splits, {"dataset_hash": "test"}),
    )
    report = v2.build_market_intelligence_v2_oos_ablation_v1(
        project_root=tmp_path,
        market_events_path=source,
        market_events_sha256=v2._source_hash(source),
        model_factory=_factory,
    )
    assert report["status"] == "ok"
    assert report["source"]["historical_reception_proven"] is False
    assert report["variants"]["FUNDING_ONLY"]["status"] == "ok"
    assert report["variants"]["BASIS_ONLY"]["reason"] == "SOURCE_UNAVAILABLE"
    assert report["variants"]["FLOW_FUNDING"]["reason"] == "SOURCE_UNAVAILABLE"
    assert report["source"]["feature_semantics"]["FUNDING"] == "mi_funding_rate_last_settled"


def test_no_br10_baseline_accepts_all_oos_without_a_selector() -> None:
    dataset, _, splits = _input()
    baseline = v2._no_br10_control(dataset, splits)
    expected = dataset.iloc[
        [index for split in splits for index in cast(list[int], split["_test_indices"])]
    ]
    assert baseline["trade_count"] == 30
    assert baseline["net_pnl_usdt"] == expected["label_economic_net_pnl"].sum()
    assert baseline["selection_identity_sha256"] == v2._selection_hash(expected)
    assert [fold["trade_count"] for fold in baseline["folds"]] == [10, 10, 10]


def test_frozen_thresholds_preserve_selection_without_recalibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    initial = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
    )
    frozen = {
        name: {fold["split_id"]: fold["threshold"] for fold in initial["variants"][name]["folds"]}
        for name in v2.VARIANTS
    }

    def forbidden(*_: object) -> dict[str, object]:
        raise AssertionError("calibration called despite frozen thresholds")

    monkeypatch.setattr(v2, "_calibrate_threshold", forbidden)
    later = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
        frozen_thresholds=frozen,
    )
    assert later["thresholds_recalibrated"] is False
    assert later["oos_used_for_variant_choice"] is False
    assert later["best_variant"] is None
    assert later["primary_economic_baseline"] == "NO_BR10_CONTROL"
    for name in v2.VARIANTS:
        assert (
            later["variants"][name]["selection_identity_sha256"]
            == initial["variants"][name]["selection_identity_sha256"]
        )
        assert (
            later["variants"][name]["selection_trade_sequences"]
            == initial["variants"][name]["selection_trade_sequences"]
        )
        assert later["variants"][name]["net_pnl_usdt"] == initial["variants"][name]["net_pnl_usdt"]


def test_missing_frozen_threshold_cannot_fall_back_to_calibration() -> None:
    dataset, events, splits = _input()
    enriched, _ = v2.align_pit_features(dataset, events)
    report = v2._evaluate_oos_variants(
        dataset=enriched,
        baseline_features=("feature_side_long",),
        splits=splits,
        model_factory=_factory,
        frozen_thresholds={"CONTROL": {}},
    )
    assert report["variants"]["CONTROL"]["reason"] == "frozen_threshold_missing_or_invalid"
    assert report["variants"]["FUNDING_ONLY"]["reason"] == "frozen_variant_thresholds_missing"


def test_filtered_outcomes_are_only_ex_post_and_gate_requires_pit_pass() -> None:
    dataset, _, splits = _input()
    baseline = v2._no_br10_control(dataset, splits)
    selected = dataset.loc[
        dataset["trade_sequence"].isin(baseline["selection_trade_sequences"][:-4])
    ]
    report = {
        "status": "ok",
        "cohort_identity_sha256": baseline["cohort_identity_sha256"],
        **v2._metrics(selected),
        "selection_trade_sequences": [int(value) for value in selected["trade_sequence"]],
        "folds": [
            {
                "split_id": split["split_id"],
                "net_pnl_usdt": baseline["folds"][index]["net_pnl_usdt"] + 1,
            }
            for index, split in enumerate(splits)
        ],
        "by_symbol": {},
        "by_side": {},
    }
    v2._compare_no_br10(report, baseline, dataset, future_join_count=1, anti_leakage_status="PASS")
    filtered = report["filtered_vs_no_br10_control"]
    assert filtered["count"] == 4
    assert [row["trade_sequence"] for row in filtered["candidates"]] == [96, 97, 98, 99]
    assert filtered["observed_net_pnl_usdt"] == dataset.loc[96:99, "label_economic_net_pnl"].sum()
    assert filtered["outcomes_used_for_decision"] is False
    assert report["economic_candidate_vs_no_br10_control"] is False
    v2._compare_no_br10(
        report, baseline, dataset, future_join_count=0, anti_leakage_status="BLOCKED"
    )
    assert report["economic_candidate_vs_no_br10_control"] is False
    v2._compare_no_br10(report, baseline, dataset, future_join_count=0, anti_leakage_status="PASS")
    assert report["economic_candidate_vs_no_br10_control"] is True
