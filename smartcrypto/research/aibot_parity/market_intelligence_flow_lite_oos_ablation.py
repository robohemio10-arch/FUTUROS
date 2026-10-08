"""Closed USD-M kline taker-flow research on the frozen WQ6 walk-forward folds."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    PUBLIC_ROOT,
    PITSourceError,
    archive_rows,
    encode,
    external_root,
    sha256,
)
from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    AblationError,
    EXPECTED_FEATURES,
    ModelFactory,
    SAFETY_FLAGS,
    _default_model_factory,
    _load_bundle,
)
from smartcrypto.research.aibot_parity.market_intelligence_v2_oos_ablation import (
    MarketIntelligenceV2Error,
    _compare_no_br10,
    _evaluate_arm,
    _no_br10_control,
)

SCHEMA = "market_intelligence_v2_taker_flow_oos_ablation_v1"
RAW_FIELDS = (
    "volume",
    "quote_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "close_time",
)
FLOW_FEATURES = (
    "taker_buy_ratio",
    "signed_taker_volume_ratio",
    "taker_buy_quote_ratio",
    "trade_count",
    "rolling_signed_flow_5m",
    "rolling_signed_flow_15m",
    "flow_acceleration_5m_vs_15m",
)
AVAILABILITY = "PIT_MODELLED_CONSERVATIVE"
KLINE_HEADER = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)


class FlowLiteError(ValueError):
    """A real closed-candle source or causal evaluation cannot be certified."""


@dataclass(frozen=True)
class FlowCandle:
    symbol: str
    open_time_ms: int
    close_time_ms: int
    volume: float
    quote_volume: float
    number_of_trades: int
    taker_buy_base_volume: float
    taker_buy_quote_volume: float
    source_url: str
    archive_sha256: str
    row_sha256: str

    @property
    def event_time_utc(self) -> datetime:
        # Binance close_time is inclusive; round UP to the finalization boundary.
        return datetime.fromtimestamp((self.close_time_ms + 1) / 1000, timezone.utc)

    @property
    def available_at_utc(self) -> datetime:
        return self.event_time_utc + timedelta(seconds=60)


def parse_klines(
    rows: Sequence[Sequence[str]],
    *,
    symbol: str,
    relative: str,
    archive_sha256: str,
    required_minutes: set[int],
) -> dict[int, FlowCandle]:
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise FlowLiteError("unsupported_symbol")
    prefix = f"futures/um/daily/klines/{symbol}/1m/{symbol}-1m-"
    if not relative.startswith(prefix) or not relative.endswith(".zip"):
        raise FlowLiteError("usd_m_kline_source_required")
    if len(archive_sha256) != 64 or any(c not in "0123456789abcdef" for c in archive_sha256):
        raise FlowLiteError("archive_hash_invalid")
    if not rows or tuple(rows[0]) != KLINE_HEADER:
        raise FlowLiteError("real_kline_fields_missing_or_schema_invalid")
    day = relative[len(prefix) : -4]
    result: dict[int, FlowCandle] = {}
    for row in rows[1:]:
        if len(row) != 12:
            raise FlowLiteError("kline_schema_invalid")
        opened, closed = int(row[0]), int(row[6])
        if opened % 60_000 or closed != opened + 59_999:
            raise FlowLiteError("partial_or_non_1m_candle")
        if datetime.fromtimestamp(opened / 1000, timezone.utc).strftime("%Y-%m-%d") != day:
            raise FlowLiteError("archive_candle_date_mismatch")
        if opened not in required_minutes:
            continue
        volume, quote, taker_base, taker_quote = [float(row[i]) for i in (5, 7, 9, 10)]
        count = int(row[8])
        if not all(math.isfinite(v) and v >= 0 for v in (volume, quote, taker_base, taker_quote)):
            raise FlowLiteError("invalid_real_volume")
        if taker_base > volume or taker_quote > quote or count < 0:
            raise FlowLiteError("invalid_taker_volume_or_trade_count")
        if opened in result:
            raise FlowLiteError("candle_identity_collision")
        result[opened] = FlowCandle(
            symbol,
            opened,
            closed,
            volume,
            quote,
            count,
            taker_base,
            taker_quote,
            PUBLIC_ROOT + relative,
            archive_sha256,
            sha256(encode(list(row))),
        )
    return result


def _minute_plan(dataset: pd.DataFrame) -> dict[tuple[str, str], set[int]]:
    required: dict[tuple[str, str], set[int]] = {}
    for row in dataset[["symbol", "open_time_utc"]].itertuples(index=False):
        if row.symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise FlowLiteError("unsupported_frozen_symbol")
        decision = pd.Timestamp(row.open_time_utc)
        if pd.isna(decision) or decision.tzinfo is None:
            raise FlowLiteError("decision_timestamp_invalid")
        for offset in range(2, 17):
            minute = decision.floor("min") - pd.Timedelta(minutes=offset)
            required.setdefault((row.symbol, minute.strftime("%Y-%m-%d")), set()).add(
                int(minute.timestamp() * 1000)
            )
    return required


def load_flow_candles(
    dataset: pd.DataFrame,
    cache_root: Path,
    *,
    allow_download: bool = False,
) -> tuple[dict[tuple[str, int], FlowCandle], dict[str, Any]]:
    candles: dict[tuple[str, int], FlowCandle] = {}
    archives: dict[str, dict[str, object]] = {}
    downloaded = 0
    plan = _minute_plan(dataset)
    for (symbol, day), minutes in sorted(plan.items()):
        relative = f"futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
        missing = (
            not (cache_root / relative).is_file()
            or not (cache_root / (relative + ".CHECKSUM")).is_file()
        )
        rows, digest = archive_rows(relative, cache_root, allow_download=allow_download and missing)
        parsed = parse_klines(
            rows,
            symbol=symbol,
            relative=relative,
            archive_sha256=digest,
            required_minutes=minutes,
        )
        for minute, candle in parsed.items():
            key = symbol, minute
            if key in candles:
                raise FlowLiteError("candle_identity_collision")
            candles[key] = candle
        archives[relative] = {
            "url": PUBLIC_ROOT + relative,
            "sha256": digest,
            "required_rows": len(minutes),
            "available_rows": len(parsed),
        }
        downloaded += int(missing and allow_download)
    return candles, {
        "real_fields_available": list(RAW_FIELDS),
        "raw_field_mapping": {
            "number_of_trades": "count",
            "taker_buy_base_volume": "taker_buy_volume",
        },
        "archives": archives,
        "archive_count": len(archives),
        "required_candle_count": sum(len(minutes) for minutes in plan.values()),
        "available_candle_count": len(candles),
        "downloaded_archive_count": downloaded,
        "cache_write_performed": downloaded > 0,
    }


def candle_features(window: Sequence[FlowCandle]) -> dict[str, float]:
    if len(window) != 15 or len({c.symbol for c in window}) != 1:
        raise FlowLiteError("consecutive_15m_window_required")
    if any(b.open_time_ms - a.open_time_ms != 60_000 for a, b in zip(window, window[1:])):
        raise FlowLiteError("rolling_window_gap")
    last = window[-1]
    if last.volume <= 0 or last.quote_volume <= 0:
        raise FlowLiteError("zero_volume_denominator")

    def signed(candles: Sequence[FlowCandle]) -> float:
        volume = sum(c.volume for c in candles)
        if volume <= 0:
            raise FlowLiteError("zero_volume_denominator")
        return sum(2 * c.taker_buy_base_volume - c.volume for c in candles) / volume

    five, fifteen = signed(window[-5:]), signed(window)
    return {
        "taker_buy_ratio": last.taker_buy_base_volume / last.volume,
        "signed_taker_volume_ratio": signed(window[-1:]),
        "taker_buy_quote_ratio": last.taker_buy_quote_volume / last.quote_volume,
        "trade_count": float(last.number_of_trades),
        "rolling_signed_flow_5m": five,
        "rolling_signed_flow_15m": fifteen,
        "flow_acceleration_5m_vs_15m": five - fifteen,
    }


def align_flow_lite(
    dataset: pd.DataFrame,
    candles: Mapping[tuple[str, int], FlowCandle],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    index: dict[str, list[FlowCandle]] = {}
    for (symbol, minute), candle in candles.items():
        if symbol != candle.symbol or minute != candle.open_time_ms:
            raise FlowLiteError("candle_identity_mismatch")
        if candle.close_time_ms != minute + 59_999 or minute % 60_000:
            raise FlowLiteError("partial_or_non_1m_candle")
        index.setdefault(symbol, []).append(candle)
    times: dict[str, list[datetime]] = {}
    for symbol, items in index.items():
        items.sort(key=lambda c: c.open_time_ms)
        times[symbol] = [c.available_at_utc for c in items]
    records: list[dict[str, object]] = []
    statuses: list[str] = []
    for row in dataset[["symbol", "open_time_utc", "feature_cutoff_utc"]].itertuples(index=False):
        decision = pd.Timestamp(row.open_time_utc).to_pydatetime()
        if pd.Timestamp(row.feature_cutoff_utc) > pd.Timestamp(row.open_time_utc):
            raise FlowLiteError("baseline_feature_after_decision")
        position = bisect_right(times.get(row.symbol, []), decision) - 1
        record: dict[str, object] = {name: None for name in FLOW_FEATURES}
        record.update(
            flow_event_time_utc=None, flow_available_at_utc=None, flow_provenance_sha256=None
        )
        status = "MISSING"
        if position >= 0:
            last = index[row.symbol][position]
            if last.available_at_utc > decision or last.event_time_utc > decision:
                raise FlowLiteError("future_join_blocked")
            if (decision - last.available_at_utc).total_seconds() >= 60:
                status = "STALE"
            else:
                window = [
                    candles.get((row.symbol, last.open_time_ms - i * 60_000))
                    for i in range(14, -1, -1)
                ]
                if all(c is not None for c in window):
                    complete = [c for c in window if c is not None]
                    try:
                        record.update(candle_features(complete))
                    except FlowLiteError as exc:
                        if str(exc) != "zero_volume_denominator":
                            raise
                        status = "ZERO_VOLUME"
                    else:
                        status = "AVAILABLE"
                        record.update(
                            flow_event_time_utc=last.event_time_utc,
                            flow_available_at_utc=last.available_at_utc,
                            flow_provenance_sha256=sha256(
                                encode(
                                    [
                                        {
                                            "url": c.source_url,
                                            "archive_sha256": c.archive_sha256,
                                            "row_sha256": c.row_sha256,
                                        }
                                        for c in complete
                                    ]
                                )
                            ),
                        )
                else:
                    status = "GAP"
        records.append(record)
        statuses.append(status)
    enriched = dataset.copy()
    frame = pd.DataFrame(records, index=dataset.index)
    for name in frame.columns:
        enriched[name] = frame[name]
    counts = {
        name: statuses.count(name)
        for name in ("AVAILABLE", "MISSING", "STALE", "GAP", "ZERO_VOLUME")
    }
    return enriched, {
        "available_count": counts["AVAILABLE"],
        "feature_coverage": counts["AVAILABLE"] / len(dataset) if len(dataset) else 0.0,
        "stale_or_unavailable_count": len(dataset) - counts["AVAILABLE"],
        "statuses": counts,
        "future_join_count": 0,
    }


def evaluate_flow_lite(
    dataset: pd.DataFrame,
    baseline_features: Sequence[str],
    splits: Sequence[Mapping[str, Any]],
    *,
    model_factory: ModelFactory = _default_model_factory,
) -> dict[str, Any]:
    if not baseline_features or set(baseline_features) - set(EXPECTED_FEATURES):
        raise FlowLiteError("feature_outside_frozen_pretrade_contract")
    if len(splits) != 3:
        raise FlowLiteError("expected_three_frozen_folds")
    all_indices = [int(i) for s in splits for i in s["_test_indices"]]
    if len(all_indices) != len(set(all_indices)):
        raise FlowLiteError("oos_fold_overlap")
    for split in splits:
        indices = [set(split[f"_{name}_indices"]) for name in ("train", "validation", "test")]
        if indices[0] & indices[1] or indices[0] & indices[2] or indices[1] & indices[2]:
            raise FlowLiteError("fold_partition_overlap")
        train, validation, test = [dataset.iloc[sorted(partition)] for partition in indices]
        if train.empty or validation.empty or test.empty:
            raise FlowLiteError("empty_frozen_fold_partition")
        if train["close_time_utc"].max() > validation["open_time_utc"].min():
            raise FlowLiteError("train_outcome_not_available_at_calibration")
        if validation["close_time_utc"].max() > test["open_time_utc"].min():
            raise FlowLiteError("calibration_outcome_not_available_at_oos")
    numeric = dataset[list(FLOW_FEATURES)].apply(pd.to_numeric, errors="coerce")
    covered = numeric.apply(
        lambda column: column.map(lambda v: pd.notna(v) and math.isfinite(v))
    ).all(axis=1)
    if not covered.all():
        raise FlowLiteError("flow_lite_complete_frozen_cohort_required")
    for row in dataset[
        ["open_time_utc", "flow_event_time_utc", "flow_available_at_utc"]
    ].itertuples(index=False):
        if pd.isna(row.flow_available_at_utc) or pd.isna(row.flow_event_time_utc):
            raise FlowLiteError("flow_availability_evidence_missing")
        if pd.Timestamp(row.flow_available_at_utc) != pd.Timestamp(
            row.flow_event_time_utc
        ) + pd.Timedelta(seconds=60):
            raise FlowLiteError("flow_availability_delay_mismatch")
        if pd.Timestamp(row.flow_available_at_utc) > pd.Timestamp(row.open_time_utc):
            raise FlowLiteError("future_join_blocked")
    baseline = _no_br10_control(dataset, splits)
    flow = _evaluate_arm(
        dataset=dataset,
        features=(*baseline_features, *FLOW_FEATURES),
        splits=splits,
        model_factory=model_factory,
        covered=covered,
    )
    flow["feature_coverage"] = 1.0
    flow["stale_or_unavailable_count"] = 0
    _compare_no_br10(flow, baseline, dataset, future_join_count=0, anti_leakage_status="PASS")
    _compare_no_br10(baseline, baseline, dataset, future_join_count=0, anti_leakage_status="PASS")
    flow["incremental_expectancy"] = flow["incremental_expectancy_vs_no_br10_control"]
    passed = bool(flow["economic_candidate_vs_no_br10_control"])
    return {
        "status": "ok",
        "decision": "ECONOMIC_CANDIDATE" if passed else "FLOW_LITE_NO_PERSISTENT_OOS_UPLIFT",
        "variants": {"NO_BR10_CONTROL": baseline, "FLOW_LITE_ONLY": flow},
        "oos_universe_count": len(all_indices),
        "folds": [s["split_id"] for s in splits],
        "selection_features": [*baseline_features, *FLOW_FEATURES],
        "parameter_selection": "past_train_and_calibration_only",
        "outcomes_used_for_selection": False,
        "oos_used_for_parameter_choice": False,
        "future_join_count": 0,
        "anti_leakage_status": "PASS",
        "point_in_time_contract": "PASS",
        "funding_recalibrated": False,
        "aggtrades_used": False,
        "phase2_recommendation": "BINANCE_USDM_AGGTRADES_RESEARCH_ONLY" if passed else None,
    }


def build_flow_lite_oos_ablation(
    *,
    project_root: str | Path,
    cache_root: str | Path,
    allow_download: bool = False,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
    model_factory: ModelFactory = _default_model_factory,
) -> dict[str, Any]:
    source: dict[str, Any] = {}
    coverage: dict[str, Any] | None = None
    common = {
        "schema_version": SCHEMA,
        "availability_classification": AVAILABILITY,
        "availability_semantics": "inclusive_close_time_rounded_up_to_1m_finalization_plus_60s",
        "rolling_semantics": "sum(2*taker_buy_base_volume-volume)/sum(volume); consecutive_closed_1m_candles",
        "acceleration_semantics": "rolling_signed_flow_5m-rolling_signed_flow_15m",
        "historical_reception_proven": False,
        "write_performed": False,
        **SAFETY_FLAGS,
    }
    try:
        cache = external_root(Path(cache_root), Path(project_root))
        dataset, baseline_features, splits, source = _load_bundle(
            project_root=project_root,
            dataset_path=dataset_path,
            feature_contract_path=feature_contract_path,
            dataset_manifest_path=dataset_manifest_path,
            split_manifest_path=split_manifest_path,
        )
        candles, audit = load_flow_candles(dataset, cache, allow_download=allow_download)
        source.update(audit)
        aligned, coverage = align_flow_lite(dataset, candles)
        oos = [int(i) for s in splits for i in s["_test_indices"]]
        coverage["oos_available_count"] = int(
            aligned.iloc[oos][list(FLOW_FEATURES)].notna().all(axis=1).sum()
        )
        coverage["oos_count"] = len(oos)
        coverage["oos_feature_coverage"] = coverage["oos_available_count"] / len(oos)
        result = evaluate_flow_lite(aligned, baseline_features, splits, model_factory=model_factory)
        return {**common, "source": source, "coverage": coverage, **result}
    except (
        AblationError,
        MarketIntelligenceV2Error,
        PITSourceError,
        FlowLiteError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        return {
            **common,
            "status": "blocked",
            "reason": str(exc),
            "decision": "FLOW_LITE_PIT_SOURCE_BLOCKED",
            "source": source,
            "coverage": coverage,
            "anti_leakage_status": "BLOCKED",
            "point_in_time_contract": "BLOCKED",
        }
