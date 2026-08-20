from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

from smartcrypto.research.financial_ai_research_engine import (
    FINANCIAL_EV_SEMANTICS,
    SAFETY_FLAGS,
    EngineConfig,
    build_financial_ai_research_engine_v1,
)
from smartcrypto.research.financial_ai_research_engine.calibration import (
    dependence_aware_expectancy_interval,
    financial_probability_metrics,
    regression_metrics,
    tie_aware_buckets,
)
from smartcrypto.research.financial_ai_research_engine.dataset import (
    SourceFrame,
    _deterministic_linkage,
    _identity_value,
    _indexed_rows,
    _shared_identity_domain,
    build_financial_training_dataset,
)
from smartcrypto.research.financial_ai_research_engine.persistence import (
    resolve_report_path,
    write_estimates_idempotent,
)


SCRIPT = Path("scripts/build_financial_ai_research_engine_v1.py")


def create_paper_db(path: Path, *, count: int = 60, missing_cost: bool = False) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, is_open INTEGER NOT NULL, pair TEXT NOT NULL,
            is_short INTEGER NOT NULL, open_date TEXT NOT NULL, close_date TEXT,
            close_profit_abs REAL, close_profit REAL, stake_amount REAL,
            open_rate REAL, close_rate REAL, max_rate REAL, min_rate REAL,
            fee_open_cost REAL, fee_close_cost REAL, funding_fees REAL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, ft_trade_id INTEGER NOT NULL,
            ft_order_side TEXT NOT NULL, ft_is_open INTEGER NOT NULL,
            status TEXT, filled REAL, remaining REAL, order_id TEXT NOT NULL
        );
        """
    )
    for trade_id in range(1, count + 1):
        opened = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=trade_id * 4)
        closed = opened + pd.Timedelta(minutes=45)
        signal = float((trade_id % 12) - 5)
        pnl = signal + (0.1 if trade_id % 2 else -0.1)
        funding = None if missing_cost and trade_id == 1 else (-0.02 if trade_id % 4 else 0.01)
        connection.execute(
            "INSERT INTO trades VALUES (?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                trade_id,
                "BTC/USDT:USDT" if trade_id % 2 else "ETH/USDT:USDT",
                trade_id % 2,
                opened.isoformat(),
                closed.isoformat(),
                pnl,
                pnl / 100.0,
                100.0,
                100.0,
                101.0,
                102.0,
                99.0,
                0.10,
                0.15,
                funding,
            ),
        )
        connection.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
            (trade_id, trade_id, "sell", 0, "closed", 1.0, 0.0, f"order-{trade_id}"),
        )
    connection.commit()
    connection.close()
    return path


def write_features(path: Path, *, count: int = 60, future: bool = False) -> Path:
    rows = []
    for trade_id in range(1, count + 1):
        decision = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=trade_id * 4)
        available = decision + pd.Timedelta(minutes=1) if future and trade_id == 1 else decision
        rows.append(
            {
                "trade_id": trade_id,
                "feature_timestamp_utc": (decision - pd.Timedelta(minutes=5)).isoformat(),
                "feature_available_at_utc": available.isoformat(),
                "momentum": float((trade_id % 12) - 5),
                "volatility": float((trade_id % 7) + 1),
                "close_profit_abs": 999.0,
                "future_ret_1": 999.0,
                "source_row_identity": f"feature-{trade_id}",
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_qlib(
    path: Path,
    *,
    count: int = 60,
    future: bool = False,
    generic_id: bool = False,
) -> Path:
    rows = []
    for trade_id in range(1, count + 1):
        decision = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=trade_id * 4)
        available = decision + pd.Timedelta(minutes=1) if future and trade_id == 1 else decision
        rows.append(
            {
                "id" if generic_id else "trade_id": trade_id,
                "score_generated_at_utc": (decision - pd.Timedelta(minutes=2)).isoformat(),
                "score_available_at_utc": available.isoformat(),
                "model_version": "qlib-research-v1",
                "qlib_score": float(trade_id),
                "prob_up": 0.9,
                "signal_confidence": 0.8,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_regime(path: Path, *, count: int = 60, future: bool = False) -> Path:
    rows = []
    for trade_id in range(1, count + 1):
        decision = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=trade_id * 4)
        available = decision + pd.Timedelta(minutes=1) if future and trade_id == 1 else decision
        rows.append(
            {
                "trade_id": trade_id,
                "regime_generated_at_utc": (decision - pd.Timedelta(minutes=5)).isoformat(),
                "regime_available_at_utc": available.isoformat(),
                "market_regime": "trend" if trade_id % 2 else "range",
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_drift(path: Path, *, critical: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "drift_summary": {"critical_drift_detected": critical},
                "feature_drift_section": {"status": "ok"},
                "qlib_performance_drift_section": {"status": "ok"},
                "regime_summary": {"overall_regime": "stable"},
            }
        ),
        encoding="utf-8",
    )
    return path


def config() -> EngineConfig:
    return EngineConfig(
        minimum_train_rows=30,
        minimum_oos_rows=12,
        minimum_positive_rows=10,
        minimum_negative_rows=10,
        embargo_seconds=60,
    )


def build(tmp_path: Path, **overrides: object) -> dict[str, object]:
    db = create_paper_db(tmp_path / "paper.sqlite")
    features = write_features(tmp_path / "features.csv")
    drift_path = tmp_path / "data/reports/ai_qlib_drift_regime_monitor_v1.json"
    if not drift_path.exists():
        write_drift(drift_path)
    kwargs: dict[str, object] = {
        "project_root": tmp_path,
        "paper_db": db,
        "feature_source": features,
        "config": config(),
    }
    kwargs.update(overrides)
    return build_financial_ai_research_engine_v1(**kwargs)


def load_cli():
    spec = importlib.util.spec_from_file_location("financial_ai_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_missing_paper_source_blocks_without_writing(tmp_path: Path) -> None:
    report = build_financial_ai_research_engine_v1(project_root=tmp_path, paper_db=None)
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "SOURCE_MISSING"
    assert report["write_performed"] is False


def test_paper_sqlite_is_read_only_and_hash_invariant(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    before = db.read_bytes()
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=write_features(tmp_path / "features.csv"),
    )
    assert len(frame) == 60
    assert db.read_bytes() == before
    assert report["sources"]["paper_db"]["source_hash_invariant"] is True


def test_point_in_time_feature_lineage_is_trainable(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=write_features(tmp_path / "features.csv"),
    )
    assert frame["trainable"].all()
    assert report["lineage"]["feature"]["point_in_time_valid_count"] == 60


def test_future_feature_is_lookahead_blocked(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv", future=True),
    )
    assert frame.loc[frame["trade_id"].eq(1), "trainable"].item() is False
    assert report["lineage"]["lookahead_blocked_rows"] == 1


def test_label_is_future_target_but_never_feature(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
    )
    assert (frame["label_available_at_utc"] > frame["decision_timestamp_utc"]).all()
    features = report["feature_contract"]["feature_columns"]
    assert "realized_net_pnl_usdt" not in features
    assert all("close_profit_abs" not in column for column in features)
    assert all("future_ret_" not in column for column in features)


def test_close_profit_abs_is_authority_without_double_subtraction(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
    )
    assert frame["realized_net_pnl_usdt"].equals(frame["reported_realized_pnl_usdt"])
    assert report["targets"]["pnl_authority"] == "FREQTRADE_CLOSE_PROFIT_ABS"
    assert report["targets"]["fees_or_funding_subtracted_again"] is False
    assert (
        report["cost_model"]["funding_sign_convention"]
        == "SOURCE_POSITIVE_REVENUE_NEGATIVE_COST"
    )


def test_cost_model_incomplete_blocks_trusted_ev(tmp_path: Path) -> None:
    report = build(
        tmp_path,
        paper_db=create_paper_db(tmp_path / "missing-cost.sqlite", missing_cost=True),
    )
    assert report["cost_model"]["cost_model_complete"] is False
    assert report["gates"]["candidate_ev_ready"] is False


def test_qlib_requires_deterministic_identity_and_point_in_time(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    features = write_features(tmp_path / "features.csv")
    _, generic = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=features,
        qlib_source=write_qlib(tmp_path / "generic.csv", generic_id=True),
    )
    assert generic["lineage"]["qlib"]["status"] == "SOURCE_UNVERIFIED"
    _, future = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=features,
        qlib_source=write_qlib(tmp_path / "future.csv", future=True),
    )
    assert future["lineage"]["qlib"]["point_in_time_valid_count"] == 59
    future_frame, _ = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=features,
        qlib_source=tmp_path / "future.csv",
    )
    assert pd.isna(future_frame.loc[future_frame["trade_id"].eq(1), "qlib_score"]).item()


def test_ordinal_fields_are_preserved_but_not_financial_targets(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
        qlib_source=write_qlib(tmp_path / "qlib.csv"),
    )
    assert frame["qlib_score"].notna().all()
    assert frame["prob_up"].eq(0.9).all()
    assert frame["signal_confidence"].eq(0.8).all()
    assert report["targets"]["primary_target"] == "realized_net_pnl_usdt"
    assert all(
        name not in report["feature_contract"]["label_columns"]
        for name in ("qlib_score", "prob_up", "signal_confidence")
    )


def test_regime_future_evidence_is_not_point_in_time_valid(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
        regime_source=write_regime(tmp_path / "regime.csv", future=True),
    )
    row = frame.loc[frame["trade_id"].eq(1)].iloc[0]
    assert row["regime"] is None
    assert report["lineage"]["regime"]["status"] == "LOOKAHEAD_BLOCKED"
    assert report["lineage"]["regime"]["point_in_time_valid_count"] == 59
    assert report["lineage"]["regime"]["linked_trade_count"] == 60


def test_trader_master_generic_id_is_not_silently_linked(tmp_path: Path) -> None:
    master = tmp_path / "master.csv"
    pd.DataFrame({"id": [1, 2], "net_pnl": [1.0, -1.0]}).to_csv(master, index=False)
    _, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
        trader_master_source=master,
    )
    comparison = report["trader_master_comparison"]
    assert comparison["trader_master_source_status"] == "TM_UNLINKED"
    assert comparison["linked_trade_count"] == 0


def test_tied_scores_do_not_create_fake_buckets() -> None:
    buckets = tie_aware_buckets([0.5] * 20, [0, 1] * 10, [1.0, -1.0] * 10)
    assert len(buckets) == 1
    assert buckets[0]["count"] == 20


def test_financial_probability_metrics_report_auc_brier_ece() -> None:
    metrics = financial_probability_metrics(
        [0.05, 0.10, 0.90, 0.95],
        [0, 0, 1, 1],
        [-1.0, -0.5, 0.5, 1.0],
        requested_bucket_count=4,
    )
    assert metrics["auc"] == 1.0
    assert metrics["brier"] < 0.02
    assert metrics["ece"] < 0.11
    assert metrics["score_semantics"] == "FINANCIAL_WIN_PROBABILITY"


def test_regression_metrics_include_spearman_top_n_and_monotonicity() -> None:
    predicted = [float(value) for value in range(1, 101)]
    metrics = regression_metrics(predicted, predicted)
    assert metrics["mae"] == 0.0
    assert metrics["rmse"] == 0.0
    assert metrics["spearman"] == pytest.approx(1.0)
    assert metrics["top_n"]["top_10_pct"]["trade_count"] == 10
    assert metrics["monotonicity"] == "MONOTONIC_NON_DECREASING"


def test_regression_top_fraction_preserves_boundary_ties() -> None:
    predicted = [1.0, 0.9, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
    realized = [1.0, 2.0, 3.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0]

    metrics = regression_metrics(predicted, realized)
    top_20 = metrics["top_n"]["top_20_pct"]

    assert top_20["requested_count"] == 2
    assert top_20["selected_count"] == 3
    assert top_20["effective_fraction"] == pytest.approx(0.3)
    assert top_20["cutoff_score"] == pytest.approx(0.9)
    assert top_20["ties_preserved"] is True
    assert top_20["trade_count"] == 3
    assert top_20["net_pnl"] == pytest.approx(6.0)


def test_qlib_comparison_top20_preserves_boundary_ties() -> None:
    import smartcrypto.research.financial_ai_research_engine.engine as engine_module

    scores = [1.0, 0.9, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
    realized = [1.0, 2.0, 3.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0]
    rows = [
        {
            "qlib_score": score,
            "financial_ev_score": score,
            "combined_score": score,
            "realized_net_pnl_usdt": pnl,
        }
        for score, pnl in zip(scores, realized, strict=True)
    ]

    comparison = engine_module._aggregate_qlib_comparison(rows)

    assert comparison["status"] == "ok"
    for experiment in comparison["experiments"].values():
        assert experiment["requested_fraction"] == pytest.approx(0.20)
        assert experiment["requested_count"] == 2
        assert experiment["selected_count"] == 3
        assert experiment["sample_count"] == 3
        assert experiment["effective_fraction"] == pytest.approx(0.3)
        assert experiment["cutoff_score"] == pytest.approx(0.9)
        assert experiment["ties_preserved"] is True
        assert experiment["trade_count"] == 3
        assert experiment["net_pnl"] == pytest.approx(6.0)


def test_walkforward_is_temporal_purged_and_embargoed(tmp_path: Path) -> None:
    report = build(tmp_path)
    walk = report["walk_forward"]
    assert walk["split_count"] == 3
    assert walk["random_split_used"] is False
    assert walk["shuffle_used"] is False
    assert walk["purge_applied"] is True
    assert walk["embargo_applied"] is True
    for fold in walk["folds"]:
        assert pd.Timestamp(fold["train_end"]) < pd.Timestamp(fold["validation_start"])
        assert pd.Timestamp(fold["validation_end"]) <= pd.Timestamp(fold["test_start"])


def test_insufficient_sample_is_fail_closed(tmp_path: Path) -> None:
    report = build_financial_ai_research_engine_v1(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite", count=12),
        feature_source=write_features(tmp_path / "features.csv", count=12),
    )
    assert report["reason"] == "INSUFFICIENT_SAMPLE"
    assert report["gates"]["candidate_ev_ready"] is False
    assert report["candidate_estimates"]["estimate_count"] == 0


def test_critical_drift_blocks_trust(tmp_path: Path) -> None:
    write_drift(tmp_path / "data/reports/ai_qlib_drift_regime_monitor_v1.json", critical=True)
    report = build(
        tmp_path,
        paper_db=create_paper_db(tmp_path / "drift.sqlite"),
        feature_source=write_features(tmp_path / "drift-features.csv"),
    )
    assert report["drift"]["overall_drift_status"] == "BLOCKED_DRIFT"
    assert report["gates"]["candidate_ev_ready"] is False
    assert all(row["candidate_ev"] is None for row in report["candidate_estimates"]["records"])


def test_candidate_estimate_contract_has_explicit_semantics_hash_and_timestamps(
    tmp_path: Path,
) -> None:
    report = build(tmp_path)
    records = report["candidate_estimates"]["records"]
    assert records
    row = records[0]
    assert row["financial_ev_semantics"] == FINANCIAL_EV_SEMANTICS
    assert len(row["financial_ev_source_hash"]) == 64
    assert row["financial_ev_generated_at_utc"].endswith("Z")
    assert row["financial_ev_available_at_utc"].endswith("Z")


def test_remaining_position_ev_is_separate_and_fail_closed(tmp_path: Path) -> None:
    report = build(tmp_path)
    remaining = report["remaining_position_estimates"]
    assert remaining["status"] == "INSUFFICIENT_TRAINING_EVIDENCE"
    assert remaining["candidate_ev_reused_as_remaining_ev"] is False
    assert remaining["record_count"] == 0
    assert remaining["records"] == []


def test_uncertainty_uses_fold_residual_dispersion(tmp_path: Path) -> None:
    report = build(tmp_path)
    uncertainty = report["uncertainty"]
    assert uncertainty["uncertainty_method"] == "fold_residual_dispersion"
    assert uncertainty["iid_bootstrap_used"] is False


def test_default_is_no_write_and_outputs_are_restricted(tmp_path: Path) -> None:
    report = build(tmp_path)
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data/reports/financial_ai_research_engine_v1.json").exists()
    with pytest.raises(ValueError, match="output_must_be_under_data_reports"):
        resolve_report_path(tmp_path, tmp_path / "outside.json")


def test_write_report_only_writes_canonical_report(tmp_path: Path) -> None:
    report = build(tmp_path, write_report_requested=True)
    target = tmp_path / "data/reports/financial_ai_research_engine_v1.json"
    assert target.exists()
    assert report["write_report_performed"] is True
    json_outputs = {
        path.name
        for path in (tmp_path / "data/reports").glob("*.json")
        if path.name != "ai_qlib_drift_regime_monitor_v1.json"
    }
    assert json_outputs == {target.name}
    assert not (tmp_path / "data/runtime").exists()


def test_persisted_report_contains_truthful_successful_write_audit(
    tmp_path: Path,
) -> None:
    report = build(tmp_path, write_report_requested=True)
    target = tmp_path / "data/reports/financial_ai_research_engine_v1.json"
    persisted = json.loads(target.read_text(encoding="utf-8"))

    audit_fields = (
        "write_requested",
        "write_performed",
        "write_report_performed",
        "write_estimates_performed",
        "estimates_appended",
    )
    assert persisted["write_requested"] is True
    assert persisted["write_performed"] is True
    assert persisted["write_report_performed"] is True
    assert persisted["write_estimates_performed"] is False
    assert persisted["estimates_appended"] == 0
    assert {
        field: persisted[field]
        for field in audit_fields
    } == {
        field: report[field]
        for field in audit_fields
    }


def test_estimate_jsonl_write_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "data/reports/estimates.jsonl"
    row = {
        "estimate_id": "estimate-1",
        "candidate_ev": None,
        "financial_ev_generated_at_utc": "2026-01-01T00:00:00Z",
    }
    assert write_estimates_idempotent(tmp_path, target, [row]) == 1
    rerun = {**row, "financial_ev_generated_at_utc": "2026-01-01T00:01:00Z"}
    with pytest.raises(ValueError, match="estimate_id_conflict"):
        write_estimates_idempotent(tmp_path, target, [rerun])
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1


def test_write_estimates_stays_under_reports(tmp_path: Path) -> None:
    report = build(
        tmp_path,
        write_estimates_requested=True,
        output_estimates="data/reports/custom-estimates.jsonl",
    )
    assert (tmp_path / "data/reports/custom-estimates.jsonl").exists()
    assert report["write_performed"] is True
    assert not (tmp_path / "data/runtime").exists()


def test_safety_flags_block_all_operational_authority(tmp_path: Path) -> None:
    report = build(tmp_path)
    for field, expected in SAFETY_FLAGS.items():
        assert report[field] is expected
        assert report["safety"][field] is expected


def test_cli_json_executes_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = load_cli()
    code = cli.main(["--project-root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "BLOCKED"
    assert payload["decision"] == "MANTER_EM_RESEARCH"
    assert payload["write_performed"] is False


def test_institutional_feature_contract_excludes_post_trade_numeric_outcomes(
    tmp_path: Path,
) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    feature_path = tmp_path / "features-post-trade.csv"
    rows = []
    for trade_id in range(1, 61):
        decision = pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=trade_id * 4)
        rows.append(
            {
                "trade_id": trade_id,
                "feature_timestamp_utc": (decision - pd.Timedelta(minutes=5)).isoformat(),
                "feature_available_at_utc": decision.isoformat(),
                "momentum": float(trade_id),
                "close_profit": 999.0,
                "realized_profit": 999.0,
                "profit_ratio": 999.0,
                "duration_seconds": 999.0,
            }
        )
    pd.DataFrame(rows).to_csv(feature_path, index=False)

    _, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=feature_path,
    )
    contract = report["feature_contract"]
    assert contract["institutional_feature_contract_reused"] is True
    assert "momentum" in contract["source_feature_columns"]
    for forbidden in ("close_profit", "realized_profit", "profit_ratio", "duration_seconds"):
        assert forbidden not in contract["source_feature_columns"]
        assert forbidden in contract["excluded_post_trade_columns"]


def test_identity_domains_never_cross_coerce_candidate_or_order_ids() -> None:
    base = pd.DataFrame(
        {
            "trade_id": [1],
            "candidate_id": ["candidate-" + "a" * 64],
            "order_id": ["order-ABC-001"],
        }
    )
    candidate_source = pd.DataFrame(
        {
            "candidate_id": ["candidate-" + "a" * 64],
            "value": [1.0],
        }
    )
    order_source = pd.DataFrame(
        {
            "order_id": ["order-ABC-001"],
            "value": [1.0],
        }
    )

    assert _shared_identity_domain(base, candidate_source) == "candidate_id"
    assert _shared_identity_domain(base, order_source) == "order_id"
    assert _identity_value("candidate-" + "a" * 64, "candidate_id") == "candidate-" + "a" * 64
    assert _identity_value("order-ABC-001", "order_id") == "order-ABC-001"
    assert _identity_value("candidate-" + "a" * 64, "trade_id") is None
    assert "candidate-" + "a" * 64 in _indexed_rows(candidate_source, "candidate_id")
    assert "order-ABC-001" in _indexed_rows(order_source, "order_id")


def test_paper_rows_do_not_fabricate_branch2_candidate_identity(tmp_path: Path) -> None:
    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=create_paper_db(tmp_path / "paper.sqlite"),
        feature_source=write_features(tmp_path / "features.csv"),
    )
    assert frame["candidate_id"].isna().all()
    assert frame["candidate_linkage_status"].eq("CANDIDATE_UNLINKED").all()
    assert report["dataset"]["candidate_linked_row_count"] == 0


def test_trader_master_candidate_id_links_only_same_identity_domain(tmp_path: Path) -> None:
    base = pd.DataFrame(
        {
            "trade_id": [1, 2],
            "candidate_id": ["candidate-A", "candidate-B"],
        }
    )
    source_frame = pd.DataFrame(
        {
            "candidate_id": ["candidate-A", "candidate-X"],
            "net_pnl": [1.0, -1.0],
        }
    )
    source = SourceFrame(
        "trader_master_source",
        "ok",
        "master.csv",
        "a" * 64,
        source_frame,
        "source_read",
    )
    comparison = _deterministic_linkage(base, source, "TM")
    assert comparison["identity_domain"] == "candidate_id"
    assert comparison["linked_trade_count"] == 1


def test_dependence_aware_expectancy_ci_is_required_and_non_iid() -> None:
    insufficient = dependence_aware_expectancy_interval([1.0] * 20)
    assert insufficient["expectancy_ci_status"] == "INSUFFICIENT_SAMPLE"
    assert insufficient["expectancy_ci_lower"] is None

    available = dependence_aware_expectancy_interval(
        [1.0 + (index % 3) * 0.01 for index in range(90)]
    )
    assert available["expectancy_ci_status"] == "AVAILABLE"
    assert available["expectancy_ci_lower"] > 0
    assert available["iid_assumption"] is False
    assert available["expectancy_ci_method"] == "CIRCULAR_MOVING_BLOCK_BOOTSTRAP"


def test_anti_leakage_gate_is_not_alias_of_walkforward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smartcrypto.research.financial_ai_research_engine.engine as engine_module

    def blocked_audit(*args, **kwargs):
        return {
            "leakage_status": "blocked",
            "temporal_overlap_count": 1,
            "train_validation_overlap_count": 1,
            "train_test_overlap_count": 0,
            "embargo_violation_count": 0,
            "label_interval_overlap_count": 1,
            "future_columns_in_features_count": 0,
            "target_columns_in_features_count": 0,
            "outcome_columns_in_features_count": 0,
            "duplicated_order_id_across_splits_count": 0,
        }

    monkeypatch.setattr(engine_module, "audit_leakage", blocked_audit)
    report = build(tmp_path)
    assert report["walk_forward"]["status"] == "ok"
    assert report["gates"]["walk_forward_valid"] is True
    assert report["gates"]["anti_leakage_valid"] is False
    assert "ANTI_LEAKAGE_BLOCKED" in report["blockers"]


def test_nonfinite_financial_label_is_fail_closed(tmp_path: Path) -> None:
    db = create_paper_db(tmp_path / "paper.sqlite")
    connection = sqlite3.connect(db)
    connection.execute("UPDATE trades SET close_profit_abs = ? WHERE id = 1", (float("inf"),))
    connection.commit()
    connection.close()

    frame, report = build_financial_training_dataset(
        project_root=tmp_path,
        paper_db=db,
        feature_source=write_features(tmp_path / "features.csv"),
    )
    row = frame.loc[frame["trade_id"].eq(1)].iloc[0]
    assert bool(row["trainable"]) is False
    assert "INVALID_FINANCIAL_LABEL" in row["lineage_errors"]
    assert report["targets"]["financial_label_valid"] is False
    assert report["dataset"]["invalid_financial_label_count"] == 1


def test_historical_oos_estimates_are_not_branch2_consumable_without_candidate_linkage(
    tmp_path: Path,
) -> None:
    report = build(tmp_path)
    records = report["candidate_estimates"]["records"]
    assert records
    assert report["candidate_estimates"]["candidate_linked_estimate_count"] == 0
    assert report["candidate_estimates"]["branch2_compatible_estimate_count"] == 0
    assert all(row["estimate_scope"] == "HISTORICAL_OOS" for row in records)
    assert all(row["candidate_id"] is None for row in records)
    assert all(row["point_in_time_consumable"] is False for row in records)
    assert all(row["branch2_compatible"] is False for row in records)


def test_evidence_hash_changes_when_drift_evidence_changes(tmp_path: Path) -> None:
    drift_path = tmp_path / "data/reports/ai_qlib_drift_regime_monitor_v1.json"
    write_drift(drift_path, critical=False)
    first = build(tmp_path)
    first_hash = first["financial_evidence"]["financial_ev_source_hash"]

    write_drift(drift_path, critical=True)
    second = build_financial_ai_research_engine_v1(
        project_root=tmp_path,
        paper_db=tmp_path / "paper.sqlite",
        feature_source=tmp_path / "features.csv",
        config=config(),
    )
    second_hash = second["financial_evidence"]["financial_ev_source_hash"]

    assert first_hash != second_hash
    assert (
        first["financial_evidence"]["dataset_hash"]
        == second["financial_evidence"]["dataset_hash"]
    )


def test_estimate_idempotency_treats_point_in_time_timestamps_as_semantic(
    tmp_path: Path,
) -> None:
    target = tmp_path / "data/reports/semantic-estimates.jsonl"
    row = {
        "estimate_id": "same-id",
        "candidate_ev": None,
        "financial_ev_generated_at_utc": "2026-01-01T00:00:00Z",
        "financial_ev_available_at_utc": "2026-01-01T00:00:00Z",
    }
    assert write_estimates_idempotent(tmp_path, target, [row]) == 1
    changed = {
        **row,
        "financial_ev_available_at_utc": "2026-01-01T00:01:00Z",
    }
    with pytest.raises(ValueError, match="estimate_id_conflict"):
        write_estimates_idempotent(tmp_path, target, [changed])


def test_write_audit_preserves_estimate_side_effect_when_report_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import smartcrypto.research.financial_ai_research_engine.engine as engine_module

    def fail_report(*args, **kwargs):
        raise OSError("forced_report_failure")

    monkeypatch.setattr(engine_module, "write_report", fail_report)
    report = build(
        tmp_path,
        write_estimates_requested=True,
        write_report_requested=True,
    )
    assert report["status"] == "BLOCKED"
    assert report["reason"] == "WRITE_FAILED:OSError"
    assert report["write_estimates_performed"] is True
    assert report["estimates_appended"] > 0
    assert report["write_report_performed"] is False
    assert report["write_performed"] is True


def test_blocked_estimates_publish_objective_blocker_list(tmp_path: Path) -> None:
    write_drift(
        tmp_path / "data/reports/ai_qlib_drift_regime_monitor_v1.json",
        critical=True,
    )
    report = build(tmp_path)
    assert "BLOCKED_DRIFT" in report["blockers"]
    assert report["candidate_estimates"]["records"]
    for row in report["candidate_estimates"]["records"]:
        assert "BLOCKED_DRIFT" in row["candidate_ev_blockers"]
        assert row["candidate_ev_status"] != "QUALITY_GATE_FAILED"


def test_remaining_position_contract_contains_no_closed_trade_subjects(tmp_path: Path) -> None:
    report = build(tmp_path)
    remaining = report["remaining_position_estimates"]
    assert remaining["status"] == "INSUFFICIENT_TRAINING_EVIDENCE"
    assert remaining["remaining_position_ev_ready"] is False
    assert remaining["record_count"] == 0
    assert remaining["records"] == []


def test_regression_gate_requires_dependence_aware_expectancy_ci(tmp_path: Path) -> None:
    report = build(tmp_path)
    active = report["regression_metrics"]["positive_predicted_ev_financial_metrics"]
    assert active["expectancy"] is not None
    assert active["expectancy"] > 0
    assert active["expectancy_ci_status"] == "INSUFFICIENT_SAMPLE"
    assert report["gates"]["regression_quality_gate"] is False
