from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.learning.quality_gated_v5_contract.anti_leakage import (
    audit_feature_names,
)
from smartcrypto.learning.quality_gated_v5_contract.contracts import (
    MODEL_FEATURES,
    PRIOR_FEATURE_SUFFIXES,
)
from smartcrypto.learning.quality_gated_v5_contract.eligibility import (
    build_eligibility,
)
from smartcrypto.learning.quality_gated_v5_contract.feature_quality import (
    audit_prior_feature_lineage,
    build_model_feature_frame,
)
from smartcrypto.learning.quality_gated_v5_contract.freshness import (
    evaluate_snapshot_freshness,
)
from smartcrypto.learning.quality_gated_v5_contract.nonregression import (
    compare_official_projection,
)
from smartcrypto.learning.quality_gated_v5_contract.projection import (
    build_quality_gated_v5_contract_report,
)
from smartcrypto.learning.quality_gated_v5_contract.provenance import (
    classify_provenance,
)


def make_market() -> pd.DataFrame:
    frames = []
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timeframe, frequency in (("1m", "1min"), ("5m", "5min")):
            timestamps = pd.date_range(
                "2026-07-01T00:00:00Z", periods=260, freq=frequency
            )
            base = 100.0 if symbol == "BTCUSDT" else 50.0
            close = base + np.linspace(0, 10, len(timestamps))
            frame = pd.DataFrame(
                {
                    "symbol": symbol,
                    "tf": timeframe,
                    "ts": timestamps,
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": np.linspace(100, 200, len(timestamps)),
                }
            )
            for suffix in PRIOR_FEATURE_SUFFIXES:
                frame[suffix] = np.linspace(1.0, 2.0, len(timestamps))
            frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def snapshot_row(market: pd.DataFrame, symbol: str, timeframe: str, ts: pd.Timestamp) -> pd.Series:
    return market[
        market["symbol"].eq(symbol)
        & market["tf"].eq(timeframe)
        & market["ts"].eq(ts)
    ].iloc[0]


def make_trade_and_market() -> tuple[pd.DataFrame, pd.DataFrame]:
    market = make_market()
    one_ts = pd.Timestamp("2026-07-01T04:19:00Z")
    five_ts = pd.Timestamp("2026-07-01T04:15:00Z")
    open_ts = pd.Timestamp("2026-07-01T04:20:30Z")
    one = snapshot_row(market, "BTCUSDT", "1m", one_ts)
    five = snapshot_row(market, "BTCUSDT", "5m", five_ts)

    trade: dict[str, object] = {
        "trade_id": "trade-v5-001",
        "symbol": "BTCUSDT",
        "fechar_side": "long",
        "open_ts": open_ts,
        "open_1m_ts": one_ts,
        "open_5m_ts": five_ts,
        "source_file": (
            "bitradex_ocr_locked_candidates_"
            "20260714_151816_time_repaired_orderid_synthetic_v5"
        ),
        "ocr_source": (
            "bitradex_black_rectangles_"
            "time_repaired_orderid_synthetic_v5"
        ),
        "segment": "BITRADEX_OCR",
    }
    for timeframe, row in (("1m", one), ("5m", five)):
        for suffix in PRIOR_FEATURE_SUFFIXES:
            value = row.get(suffix)
            if value is None or pd.isna(value):
                value = 1.0
            trade[f"open_{timeframe}_{suffix}"] = value
    return pd.DataFrame([trade]), market


def write_environment(root: Path) -> tuple[str, dict[str, Path]]:
    trades, market = make_trade_and_market()
    fixtures = root / "data" / "fixtures"
    models = root / "data" / "models"
    fixtures.mkdir(parents=True)
    models.mkdir(parents=True)
    trade_path = fixtures / "trade_enriched.json"
    market_path = fixtures / "market_features.json"
    official_path = fixtures / "official_quality_gated.json"
    trades.to_json(trade_path, orient="records", date_format="iso")
    market.to_json(market_path, orient="records", date_format="iso")
    pd.DataFrame({"trade_id": ["trade-v5-001"]}).to_json(
        official_path, orient="records"
    )
    model_path = models / "ai_shadow_filter_extratrees_050.joblib"
    model_path.write_bytes(b"not-a-joblib-object-and-must-never-be-deserialized")
    return hashlib.sha256(model_path.read_bytes()).hexdigest(), {
        "trade": trade_path,
        "market": market_path,
        "official": official_path,
        "model": model_path,
    }


def environment_kwargs(root: Path) -> tuple[str, dict[str, Path], dict[str, object]]:
    model_hash, paths = write_environment(root)
    kwargs: dict[str, object] = {
        "project_root": root,
        "trade_enriched_path": paths["trade"],
        "market_features_path": paths["market"],
        "official_quality_gated_path": paths["official"],
        "model_path": paths["model"],
        "expected_model_sha256": model_hash,
        "expected_v5_rows": 1,
    }
    return model_hash, paths, kwargs


def test_model_contract_has_exactly_74_ordered_features() -> None:
    assert len(MODEL_FEATURES) == 74
    assert len(set(MODEL_FEATURES)) == 74
    assert MODEL_FEATURES[0] == "prior_1m_ret_1"
    assert MODEL_FEATURES[-1] == "v13_5m_volume_z_50"


@pytest.mark.parametrize(
    ("row", "contract_id", "status"),
    [
        (
            {"source_file": "bitradex_ocr_locked_candidates_20260528_090243"},
            "legacy_v1",
            "ok",
        ),
        (
            {"ocr_source": "bitradex_ocr_candidate_v1_1"},
            "ocr_v11",
            "ok",
        ),
        (
            {
                "source_file": (
                    "bitradex_ocr_locked_candidates_"
                    "20260714_151816_time_repaired_orderid_synthetic_v5"
                ),
                "ocr_source": (
                    "bitradex_black_rectangles_"
                    "time_repaired_orderid_synthetic_v5"
                ),
                "segment": "BITRADEX_OCR",
            },
            "ocr_v5_20260714",
            "ok",
        ),
        ({"source_file": "", "ocr_source": "", "segment": "HISTORICAL"}, "historical", "ok"),
    ],
)
def test_exact_provenance_contracts(row: dict, contract_id: str, status: str) -> None:
    result = classify_provenance(row)
    assert result.contract_id == contract_id
    assert result.status == status


def test_partial_v5_provenance_is_blocked() -> None:
    result = classify_provenance(
        {
            "source_file": (
                "bitradex_ocr_locked_candidates_"
                "20260714_151816_time_repaired_orderid_synthetic_v5"
            ),
            "ocr_source": "wrong",
            "segment": "BITRADEX_OCR",
        }
    )
    assert result.contract_id == "PARTIAL"
    assert result.block_reasons == ("BLOCKED_PARTIAL_PROVENANCE",)


def test_unknown_explicit_ocr_provenance_is_blocked_without_contains_matching() -> None:
    result = classify_provenance(
        {
            "source_file": "bitradex-similar-but-not-contracted",
            "ocr_source": "other",
            "segment": "BITRADEX_OCR",
        }
    )
    assert result.contract_id == "UNKNOWN"
    assert "BLOCKED_UNRECOGNIZED_PROVENANCE" in result.block_reasons


def test_recent_closed_snapshot_is_accepted() -> None:
    result = evaluate_snapshot_freshness(
        trade_open_time="2026-07-01T12:01:30Z",
        snapshot_time="2026-07-01T12:00:00Z",
        timeframe="1m",
    )
    assert result.status == "ok"
    assert result.snapshot_age_seconds == 30.0


@pytest.mark.parametrize(
    ("trade_open", "snapshot", "expected"),
    [
        ("2026-07-01T12:10:00Z", "2026-07-01T12:00:00Z", "BLOCKED_STALE_1M_SNAPSHOT"),
        ("2026-07-01T12:00:00Z", "2026-07-01T12:01:00Z", "BLOCKED_FUTURE_1M_SNAPSHOT"),
        ("2026-07-01T12:00:30Z", "2026-07-01T12:00:00Z", "BLOCKED_IN_PROGRESS_1M_SNAPSHOT"),
    ],
)
def test_snapshot_temporal_failures(
    trade_open: str, snapshot: str, expected: str
) -> None:
    result = evaluate_snapshot_freshness(
        trade_open_time=trade_open,
        snapshot_time=snapshot,
        timeframe="1m",
    )
    assert result.status == "blocked"
    assert expected in result.block_reasons


def test_missing_and_unknown_semantics_are_fail_closed() -> None:
    missing = evaluate_snapshot_freshness(
        trade_open_time="2026-07-01T12:01:30Z",
        snapshot_time=None,
        timeframe="1m",
    )
    unknown = evaluate_snapshot_freshness(
        trade_open_time="2026-07-01T12:01:30Z",
        snapshot_time="2026-07-01T12:00:00Z",
        timeframe="1m",
        timestamp_semantics="unknown",
    )
    assert "BLOCKED_MISSING_1M_SNAPSHOT" in missing.block_reasons
    assert "BLOCKED_UNKNOWN_SNAPSHOT_TIMESTAMP_SEMANTICS" in unknown.block_reasons


@pytest.mark.parametrize(
    "feature",
    [
        "target_win",
        "reported_pnl_usdt",
        "future_ret_1",
        "label_quality",
        "mfe_pct",
        "close_time_utc",
    ],
)
def test_leakage_names_are_blocked(feature: str) -> None:
    report = audit_feature_names(["prior_1m_ret_1", feature])
    assert report["status"] == "blocked"
    assert feature in report["forbidden_features"]


def test_multiple_block_reasons_are_preserved_with_deterministic_primary() -> None:
    trades = pd.DataFrame(
        {
            "trade_id": ["dup", "dup"],
            "open_ts": [pd.NaT, pd.NaT],
        }
    )
    provenance = pd.DataFrame(
        {
            "provenance_block_reasons": [
                ["BLOCKED_UNRECOGNIZED_PROVENANCE"],
                ["BLOCKED_UNRECOGNIZED_PROVENANCE"],
            ]
        }
    )
    freshness = pd.DataFrame(
        {
            "snapshot_1m_block_reasons": [["BLOCKED_STALE_1M_SNAPSHOT"]] * 2,
            "snapshot_5m_block_reasons": [["BLOCKED_MISSING_5M_SNAPSHOT"]] * 2,
        }
    )
    feature_quality = pd.DataFrame(
        {"feature_block_reasons": [["BLOCKED_MISSING_PRIOR_5M_FEATURES"]] * 2}
    )
    temporal = pd.DataFrame({"temporal_leakage_block_reasons": [[], []]})
    result = build_eligibility(
        trades,
        provenance,
        freshness,
        feature_quality,
        temporal,
        feature_name_audit={"block_reasons": []},
    )
    assert result.loc[0, "primary_reason"] == "BLOCKED_DUPLICATE_TRADE_ID"
    assert len(result.loc[0, "block_reasons"]) >= 5


def test_nonregression_is_set_based_not_count_based() -> None:
    official = pd.DataFrame({"trade_id": ["a", "b"]})
    projection = pd.DataFrame(
        {
            "trade_id": ["a", "b", "c"],
            "eligible_for_model_training": [True, False, True],
            "block_reasons": [[], ["BLOCKED_STALE_1M_SNAPSHOT"], []],
        }
    )
    result = compare_official_projection(official, projection)
    assert result["projected_rows"] == 2
    assert result["official_ids_blocked"] == 1
    assert result["unexplained_removed_official_ids"] == 0
    assert result["status"] == "review_required"


def test_unexplained_removed_identity_blocks() -> None:
    official = pd.DataFrame({"trade_id": ["a", "b"]})
    projection = pd.DataFrame(
        {
            "trade_id": ["a", "b"],
            "eligible_for_model_training": [True, False],
            "block_reasons": [[], []],
        }
    )
    result = compare_official_projection(official, projection)
    assert result["status"] == "blocked"
    assert result["unexplained_removed_official_trade_ids"] == ["b"]


def test_lineage_distinguishes_raw_source_from_materialization_gap() -> None:
    trades, market = make_trade_and_market()
    features, snapshots = build_model_feature_frame(trades, market)
    assert features.shape == (1, 74)
    trades.loc[0, "open_5m_ret_1"] = np.nan
    lineage = audit_prior_feature_lineage(trades, snapshots)
    assert "open_5m_ret_1" in lineage.loc[0, "materialized_5m_features_missing"]


def test_projection_default_no_write_and_no_model_deserialization(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    report = build_quality_gated_v5_contract_report(
        **kwargs,
        generated_at_utc="2026-07-17T00:00:00+00:00",
    )
    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert report["model_contract"]["model_deserialization_performed"] is False
    assert report["model_contract"]["hash_match"] is True
    assert report["universe_rows"] == 1
    assert report["row_detail_records"] == 1
    assert not (tmp_path / "data" / "reports").exists()
    assert not list(tmp_path.rglob("*candidate*.parquet"))


def test_write_report_creates_only_report_files(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    before_sources = {path: path.read_bytes() for path in paths.values()}
    report = build_quality_gated_v5_contract_report(
        **kwargs,
        write_report=True,
        generated_at_utc="2026-07-17T00:00:00+00:00",
    )
    assert report["write_performed"] is True
    reports = tmp_path / "data" / "reports"
    assert sorted(path.suffix for path in reports.iterdir()) == [".json", ".jsonl", ".md"]
    assert not list(tmp_path.rglob("*.sqlite"))
    for path, payload in before_sources.items():
        assert path.read_bytes() == payload


def test_report_paths_outside_data_reports_are_rejected(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    with pytest.raises(ValueError, match="report_path_outside_data_reports"):
        build_quality_gated_v5_contract_report(
            **kwargs,
            write_report=True,
            report_json_path=tmp_path / "forbidden.json",
        )


def test_cli_no_write_precedes_write_report(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_quality_gated_v5_provenance_freshness_nonregression_contract_v1.py",
            "--project-root",
            str(tmp_path),
            "--trade-enriched",
            str(paths["trade"]),
            "--market-features",
            str(paths["market"]),
            "--official-quality-gated",
            str(paths["official"]),
            "--model-path",
            str(paths["model"]),
            "--write-report",
            "--no-write",
            "--expected-v5-rows",
            "1",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    assert payload["write_requested"] is False
    assert payload["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()
    assert model_hash != payload["model_contract"]["expected_sha256"]


def test_evidence_hash_is_deterministic(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    kwargs = {
        **kwargs,
        "generated_at_utc": "2026-07-17T00:00:00+00:00",
    }
    first = build_quality_gated_v5_contract_report(**kwargs)
    second = build_quality_gated_v5_contract_report(**kwargs)
    assert first["evidence_hash"] == second["evidence_hash"]


def test_all_operational_authority_flags_remain_false(tmp_path: Path) -> None:
    model_hash, paths, kwargs = environment_kwargs(tmp_path)
    report = build_quality_gated_v5_contract_report(**kwargs)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "writes_runtime",
        "writes_sqlite",
        "writes_official",
        "writes_candidate",
        "writes_full_audit",
        "changes_model",
        "training_performed",
        "qlib_training_performed",
        "ai_shadow_training_performed",
        "registry_write_performed",
        "model_promotion_performed",
        "active_model_changed",
        "services_started",
    ):
        assert report[key] is False
