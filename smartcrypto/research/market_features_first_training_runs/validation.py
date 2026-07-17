"""Fail-closed environment, input, feature, and concept-drift validation."""

from __future__ import annotations

import platform
import re
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
import sklearn

from .contracts import (
    DRIFT_CUTOFF_UTC,
    FORBIDDEN_EXACT_FEATURES,
    FORBIDDEN_FEATURE_PREFIXES,
    LOOKAHEAD_COLUMN_PREFIXES,
    LOOKAHEAD_EXACT_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    PAPER_V1_WATERMARK_UTC,
    TIMEFRAME,
    TIMEFRAME_SECONDS,
    RuntimeEnvironment,
    canonical_environment,
)


_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def evaluate_canonical_environment(
    override: RuntimeEnvironment | None = None,
) -> dict[str, Any]:
    """Allow diagnostics everywhere but financial execution only on exact pins."""

    observed = override or RuntimeEnvironment(
        python_version=platform.python_version(),
        sklearn_version=str(sklearn.__version__),
        joblib_version=str(joblib.__version__),
    )
    expected = canonical_environment()
    checks = {
        "python_version_matches": observed.python_version == expected.python_version,
        "sklearn_version_matches": observed.sklearn_version == expected.sklearn_version,
        "joblib_version_matches": observed.joblib_version == expected.joblib_version,
    }
    compatible = all(checks.values())
    return {
        "status": "ok" if compatible else "blocked",
        "reason": (
            "canonical_training_environment_confirmed"
            if compatible
            else "canonical_training_environment_mismatch"
        ),
        "expected": {
            "python": expected.python_version,
            "scikit_learn": expected.sklearn_version,
            "joblib": expected.joblib_version,
        },
        "observed": {
            "python": observed.python_version,
            "scikit_learn": observed.sklearn_version,
            "joblib": observed.joblib_version,
        },
        **checks,
        "diagnostics_allowed": True,
        "training_allowed": compatible,
        "backtest_allowed": compatible,
        "monte_carlo_allowed": compatible,
    }


def normalize_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    for token in ("/", "_", "-", ":USDT"):
        text = text.replace(token, "")
    return text if text in {"BTCUSDT", "ETHUSDT"} else None


def normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if "short" in text or "venda" in text:
        return "short"
    if "long" in text or "compra" in text:
        return "long"
    return None


def parse_number(value: Any) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float, np.number)):
        return float(value)
    match = _NUMBER.search(str(value).replace(" ", ""))
    if match is None:
        return float("nan")
    token = match.group(0)
    if token.count(",") == 1 and token.count(".") == 0:
        token = token.replace(",", ".")
    return float(token)


def utc_series(values: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(values, utc=True, errors="coerce", format="mixed")
    except TypeError:  # pandas < 2 compatibility
        return pd.to_datetime(values, utc=True, errors="coerce")


def normalize_master(master: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    required = {
        "moeda",
        "fechar_side",
        "pnl_fechado",
        "horario_abertura",
        "horario_fechamento",
    }
    missing = sorted(required - set(master.columns))
    if missing:
        raise ValueError("master_required_columns_missing:" + ",".join(missing))
    frame = pd.DataFrame(index=master.index)
    frame["source_row_number"] = np.arange(len(master), dtype=int)
    frame["trade_id"] = _stable_trade_ids(master)
    frame["symbol"] = master["moeda"].map(normalize_symbol)
    frame["side"] = master["fechar_side"].map(normalize_side)
    frame["open_time_utc"] = utc_series(master["horario_abertura"])
    frame["close_time_utc"] = utc_series(master["horario_fechamento"])
    frame["net_pnl"] = master["pnl_fechado"].map(parse_number)
    frame["target_profitable"] = frame["net_pnl"].gt(0).astype("Int64")
    fee_one = master.get("taxa_1", pd.Series(index=master.index, dtype=object)).map(
        parse_number
    )
    fee_two = master.get("taxa_2", pd.Series(index=master.index, dtype=object)).map(
        parse_number
    )
    frame["observed_cost"] = fee_one.abs().fillna(0.0) + fee_two.abs().fillna(0.0)
    source_file = master.get(
        "source_file", pd.Series("unknown", index=master.index, dtype="string")
    ).fillna("unknown").astype(str)
    ocr_source = master.get(
        "ocr_source", pd.Series(pd.NA, index=master.index, dtype="string")
    )
    frame["provenance"] = source_file
    frame["ocr_source_diagnostic"] = ocr_source
    v2_marker = source_file.str.casefold().str.contains("20260714|synthetic_v5", regex=True)
    frame["is_ocr_v2_tail"] = v2_marker
    frame["is_ocr"] = ocr_source.notna() & ocr_source.astype(str).str.strip().ne("")
    frame["dataset_partition"] = "master_research_fit_candidate"
    blockers = _validate_trade_rows(frame, dataset="master")
    return frame, blockers


def normalize_paper(paper: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    aliases = {
        "trade_id": ("stable_trade_id", "trade_id", "id"),
        "symbol": ("symbol", "pair"),
        "side": ("side",),
        "open_time_utc": ("open_time_utc", "open_date"),
        "close_time_utc": ("close_time_utc", "close_date"),
        "net_pnl": ("net_pnl", "close_profit_abs", "realized_profit"),
    }
    frame = pd.DataFrame(index=paper.index)
    for target, candidates in aliases.items():
        source = next((name for name in candidates if name in paper.columns), None)
        frame[target] = paper[source] if source else pd.NA
    frame["source_row_number"] = np.arange(len(paper), dtype=int)
    frame["trade_id"] = frame["trade_id"].map(
        lambda value: str(value) if pd.notna(value) else None
    )
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    if frame["side"].isna().all() and "is_short" in paper.columns:
        frame["side"] = paper["is_short"].map({True: "short", False: "long"})
    frame["side"] = frame["side"].map(normalize_side)
    frame["open_time_utc"] = utc_series(frame["open_time_utc"])
    frame["close_time_utc"] = utc_series(frame["close_time_utc"])
    frame["net_pnl"] = frame["net_pnl"].map(parse_number)
    frame["target_profitable"] = frame["net_pnl"].gt(0).astype("Int64")
    open_fee = paper.get("fee_open_cost", pd.Series(index=paper.index, dtype=float)).map(
        parse_number
    )
    close_fee = paper.get(
        "fee_close_cost", pd.Series(index=paper.index, dtype=float)
    ).map(parse_number)
    funding = paper.get("funding_fees", pd.Series(index=paper.index, dtype=float)).map(
        parse_number
    )
    frame["observed_cost"] = (
        open_fee.abs().fillna(0.0)
        + close_fee.abs().fillna(0.0)
        + funding.abs().fillna(0.0)
    )
    frame["provenance"] = "freqtrade_paper_snapshot"
    frame["ocr_source_diagnostic"] = pd.NA
    frame["is_ocr_v2_tail"] = False
    frame["is_ocr"] = False
    frame["dataset_partition"] = "paper_external_holdout"
    inherited_eligible = paper.get(
        "analysis_eligible", pd.Series(True, index=paper.index, dtype=bool)
    ).fillna(False)
    inherited_reason = paper.get(
        "analysis_block_reason", pd.Series(pd.NA, index=paper.index, dtype="string")
    )
    inherited: dict[int, list[str]] = {int(index): [] for index in frame.index}
    for index in frame.index[~inherited_eligible.astype(bool)]:
        value = inherited_reason.loc[index]
        inherited[int(index)].append(
            str(value) if pd.notna(value) else "paper_snapshot_ineligible"
        )
    blockers = _validate_trade_rows(frame, dataset="paper", inherited=inherited)
    return frame, blockers


def normalize_5m_features(features: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "tf", "ts", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError("market_feature_columns_missing:" + ",".join(missing))
    lookahead = lookahead_columns(features.columns)
    if lookahead:
        raise ValueError("market_feature_lookahead_columns:" + ",".join(lookahead))
    frame = features.loc[
        features["tf"].astype(str).str.casefold().eq(TIMEFRAME),
        ["symbol", "tf", "ts", "open", "high", "low", "close", "volume"],
    ].copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["candle_timestamp_utc"] = utc_series(frame.pop("ts"))
    frame["available_at_utc"] = frame["candle_timestamp_utc"] + pd.Timedelta(
        seconds=TIMEFRAME_SECONDS
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(
        subset=[
            "symbol",
            "candle_timestamp_utc",
            "available_at_utc",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )
    valid_ohlc = (
        frame["high"].ge(frame[["open", "close", "low"]].max(axis=1))
        & frame["low"].le(frame[["open", "close", "high"]].min(axis=1))
    )
    frame = frame.loc[valid_ohlc]
    frame = frame.sort_values(["symbol", "candle_timestamp_utc"], kind="mergesort")
    frame = frame.drop_duplicates(["symbol", "candle_timestamp_utc"], keep="last")
    frame = frame.reset_index(drop=True)
    frame["contiguous_segment_id"] = frame.groupby("symbol", sort=False)[
        "candle_timestamp_utc"
    ].transform(
        lambda values: values.diff().ne(pd.Timedelta(seconds=TIMEFRAME_SECONDS)).cumsum()
    )
    segments = [
        _rematerialize_segment(segment.copy())
        for _, segment in frame.groupby(
            ["symbol", "contiguous_segment_id"], sort=False, dropna=False
        )
    ]
    return pd.concat(segments, ignore_index=True) if segments else frame


def forbidden_feature_columns(columns: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for column in columns:
        name = str(column).casefold()
        if name in FORBIDDEN_EXACT_FEATURES or name.startswith(FORBIDDEN_FEATURE_PREFIXES):
            result.append(str(column))
    return sorted(set(result))


def lookahead_columns(columns: Iterable[Any]) -> list[str]:
    """Detect post-entry data while allowing non-model source metadata."""

    result: list[str] = []
    for column in columns:
        name = str(column).casefold()
        if name in LOOKAHEAD_EXACT_COLUMNS or name.startswith(
            LOOKAHEAD_COLUMN_PREFIXES
        ):
            result.append(str(column))
    return sorted(set(result))


def build_concept_drift_report(master: pd.DataFrame, paper: pd.DataFrame) -> dict[str, Any]:
    """Compare temporal and provenance cohorts without feeding provenance to models."""

    ready_master = master.loc[master["row_status"].eq("ready")].copy()
    ready_paper = paper.loc[paper["row_status"].eq("ready")].copy()
    cutoff = pd.Timestamp(DRIFT_CUTOFF_UTC)
    watermark = pd.Timestamp(PAPER_V1_WATERMARK_UTC)
    cohorts = {
        "master_before_2026_06_10": ready_master.loc[
            ready_master["open_time_utc"].lt(cutoff)
        ],
        "master_after_2026_06_10": ready_master.loc[
            ready_master["open_time_utc"].ge(cutoff)
        ],
        "ocr_v2_tail": ready_master.loc[ready_master["is_ocr_v2_tail"]],
        "historical_pre_v2": ready_master.loc[~ready_master["is_ocr_v2_tail"]],
        "historical_non_ocr": ready_master.loc[~ready_master["is_ocr"]],
        "paper_evaluation_set_v1_consumed": ready_paper.loc[
            ready_paper["close_time_utc"].le(watermark)
        ],
        "prospective_holdout_v2": ready_paper.loc[
            ready_paper["close_time_utc"].gt(watermark)
        ],
    }
    comparison_specs = (
        ("master_temporal", "master_before_2026_06_10", "master_after_2026_06_10"),
        ("ocr_v2_tail_vs_history", "historical_pre_v2", "ocr_v2_tail"),
        ("ocr_v2_vs_non_ocr", "historical_non_ocr", "ocr_v2_tail"),
        (
            "master_after_cutoff_vs_paper_v1",
            "master_after_2026_06_10",
            "paper_evaluation_set_v1_consumed",
        ),
    )
    comparisons = [
        _compare_cohorts(name, cohorts[reference], cohorts[target], reference, target)
        for name, reference, target in comparison_specs
    ]
    return {
        "status": "ok" if any(item["status"] == "ok" for item in comparisons) else "warning",
        "reason": "concept_drift_diagnostics_completed",
        "cutoff_utc": cutoff.isoformat(),
        "paper_v1_watermark_utc": watermark.isoformat(),
        "feature_columns": list(MODEL_FEATURE_COLUMNS),
        "provenance_used_as_feature": False,
        "cohort_counts": {name: int(len(frame)) for name, frame in cohorts.items()},
        "comparisons": comparisons,
        "decomposition": {
            "master": _decompose(ready_master),
            "paper_v1": _decompose(cohorts["paper_evaluation_set_v1_consumed"]),
        },
        "warnings": [
            f"insufficient_cohort:{name}"
            for name, frame in cohorts.items()
            if len(frame) < 5
        ],
    }


def _compare_cohorts(
    name: str,
    reference: pd.DataFrame,
    target: pd.DataFrame,
    reference_name: str,
    target_name: str,
) -> dict[str, Any]:
    base = {
        "comparison_id": name,
        "reference_cohort": reference_name,
        "target_cohort": target_name,
        "reference_rows": int(len(reference)),
        "target_rows": int(len(target)),
    }
    if len(reference) < 5 or len(target) < 5:
        return {**base, "status": "insufficient_data", "feature_metrics": []}
    feature_metrics: list[dict[str, Any]] = []
    for column in MODEL_FEATURE_COLUMNS:
        left = _finite(reference[column])
        right = _finite(target[column])
        if len(left) < 5 or len(right) < 5:
            feature_metrics.append(
                {"feature": column, "status": "insufficient_data", "psi": None, "ks": None, "wasserstein": None}
            )
            continue
        feature_metrics.append(
            {
                "feature": column,
                "status": "ok",
                "psi": population_stability_index(left, right),
                "ks": kolmogorov_smirnov_statistic(left, right),
                "wasserstein": wasserstein_distance(left, right),
            }
        )
    left_label = reference["target_profitable"].astype(float)
    right_label = target["target_profitable"].astype(float)
    left_pnl = reference["net_pnl"].astype(float)
    right_pnl = target["net_pnl"].astype(float)
    valid = [item for item in feature_metrics if item["status"] == "ok"]
    return {
        **base,
        "status": "ok",
        "feature_metrics": feature_metrics,
        "mean_psi": float(np.mean([item["psi"] for item in valid])) if valid else None,
        "max_ks": float(max(item["ks"] for item in valid)) if valid else None,
        "mean_wasserstein": (
            float(np.mean([item["wasserstein"] for item in valid])) if valid else None
        ),
        "label_drift": {
            "reference_positive_rate": float(left_label.mean()),
            "target_positive_rate": float(right_label.mean()),
            "positive_rate_delta": float(right_label.mean() - left_label.mean()),
        },
        "pnl_drift": {
            "reference_mean_net_pnl": float(left_pnl.mean()),
            "target_mean_net_pnl": float(right_pnl.mean()),
            "mean_net_pnl_delta": float(right_pnl.mean() - left_pnl.mean()),
            "reference_median_net_pnl": float(left_pnl.median()),
            "target_median_net_pnl": float(right_pnl.median()),
        },
    }


def population_stability_index(reference: np.ndarray, target: np.ndarray) -> float:
    quantiles = np.unique(np.quantile(reference, np.linspace(0.0, 1.0, 11)))
    if len(quantiles) < 3:
        return 0.0 if np.isclose(np.mean(reference), np.mean(target)) else 1.0
    quantiles[0] = -np.inf
    quantiles[-1] = np.inf
    ref_counts = np.histogram(reference, bins=quantiles)[0].astype(float)
    target_counts = np.histogram(target, bins=quantiles)[0].astype(float)
    epsilon = 1e-6
    ref_share = np.maximum(ref_counts / max(1.0, ref_counts.sum()), epsilon)
    target_share = np.maximum(target_counts / max(1.0, target_counts.sum()), epsilon)
    return float(np.sum((target_share - ref_share) * np.log(target_share / ref_share)))


def kolmogorov_smirnov_statistic(reference: np.ndarray, target: np.ndarray) -> float:
    points = np.sort(np.unique(np.concatenate((reference, target))))
    left = np.searchsorted(np.sort(reference), points, side="right") / len(reference)
    right = np.searchsorted(np.sort(target), points, side="right") / len(target)
    return float(np.max(np.abs(left - right)))


def wasserstein_distance(reference: np.ndarray, target: np.ndarray) -> float:
    grid = np.linspace(0.0, 1.0, max(len(reference), len(target)))
    return float(np.mean(np.abs(np.quantile(reference, grid) - np.quantile(target, grid))))


def _decompose(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if frame.empty:
        return {name: [] for name in ("symbol", "side", "week", "provenance")}
    work = frame.copy()
    work["week"] = work["open_time_utc"].dt.strftime("%G-W%V")
    result: dict[str, list[dict[str, Any]]] = {}
    for dimension in ("symbol", "side", "week", "provenance"):
        records: list[dict[str, Any]] = []
        for value, group in work.groupby(dimension, dropna=False, sort=True):
            records.append(
                {
                    dimension: str(value),
                    "row_count": int(len(group)),
                    "positive_rate": float(group["target_profitable"].astype(float).mean()),
                    "net_pnl": float(group["net_pnl"].sum()),
                    "mean_net_pnl": float(group["net_pnl"].mean()),
                }
            )
        result[dimension] = records
    return result


def _rematerialize_segment(segment: pd.DataFrame) -> pd.DataFrame:
    close = segment["close"].astype(float)
    high = segment["high"].astype(float)
    low = segment["low"].astype(float)
    volume = segment["volume"].astype(float)
    for periods in (1, 3, 5, 10, 15):
        segment[f"ret_{periods}"] = close.pct_change(periods, fill_method=None)
    ema_20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
    ema_50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ema_200 = close.ewm(span=200, adjust=False, min_periods=200).mean()
    segment["dist_ema20"] = close.div(ema_20).sub(1.0)
    segment["dist_ema50"] = close.div(ema_50).sub(1.0)
    segment["dist_ema200"] = close.div(ema_200).sub(1.0)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = -delta.clip(upper=0).rolling(14, min_periods=14).mean()
    relative_strength = gain.div(loss.replace(0.0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    segment["rsi_14"] = rsi.mask(loss.eq(0.0) & gain.gt(0.0), 100.0).mask(
        loss.eq(0.0) & gain.eq(0.0), 50.0
    )
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema_12 - ema_26
    segment["macd_hist"] = macd - macd.ewm(span=9, adjust=False, min_periods=9).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    segment["atr_pct_14"] = true_range.rolling(14, min_periods=14).mean().div(close)
    returns = close.pct_change(fill_method=None)
    segment["vol_30"] = returns.rolling(30, min_periods=30).std(ddof=0)
    segment["vol_120"] = returns.rolling(120, min_periods=120).std(ddof=0)
    volume_mean = volume.rolling(30, min_periods=30).mean()
    volume_std = volume.rolling(30, min_periods=30).std(ddof=0).replace(0.0, np.nan)
    segment["volume_rel_30"] = volume.div(volume_mean)
    segment["volume_z_30"] = volume.sub(volume_mean).div(volume_std)
    segment["trend_score"] = np.sign(ema_20 - ema_50)
    segment["market_regime"] = np.select(
        [segment["trend_score"].gt(0), segment["trend_score"].lt(0)],
        ["trend_up", "trend_down"],
        default="range",
    )
    segment["volatility_regime"] = np.select(
        [segment["atr_pct_14"].lt(0.001), segment["atr_pct_14"].gt(0.003)],
        ["low", "high"],
        default="normal",
    )
    segment.loc[segment["atr_pct_14"].isna(), "volatility_regime"] = "unknown"
    return segment


def _validate_trade_rows(
    frame: pd.DataFrame,
    *,
    dataset: str,
    inherited: dict[int, list[str]] | None = None,
) -> list[dict[str, Any]]:
    reasons = inherited or {int(index): [] for index in frame.index}
    for index in frame.index:
        reasons.setdefault(int(index), [])
    _record_missing(frame, "symbol", "invalid_symbol", reasons)
    _record_missing(frame, "side", "invalid_side", reasons)
    _record_missing(frame, "open_time_utc", "invalid_open_time", reasons)
    _record_missing(frame, "close_time_utc", "invalid_close_time", reasons)
    _record_nonfinite(frame, "net_pnl", "invalid_net_pnl", reasons)
    invalid_interval = (
        frame["open_time_utc"].notna()
        & frame["close_time_utc"].notna()
        & frame["close_time_utc"].lt(frame["open_time_utc"])
    )
    for index in frame.index[invalid_interval]:
        reasons[int(index)].append("close_before_open")
    frame["validation_block_reasons"] = [
        tuple(sorted(set(reasons[int(index)]))) for index in frame.index
    ]
    frame["row_status"] = frame["validation_block_reasons"].map(
        lambda values: "blocked" if values else "eligible_for_alignment"
    )
    return [
        _blocker(dataset, row, str(reason))
        for _, row in frame.iterrows()
        for reason in row["validation_block_reasons"]
    ]


def _stable_trade_ids(master: pd.DataFrame) -> pd.Series:
    candidates = ("order_id", "_dedup_key", "_relaxed_dedup_key")
    values: list[str] = []
    for index, row in master.iterrows():
        selected = next(
            (
                str(row[name]).strip()
                for name in candidates
                if name in master.columns
                and pd.notna(row[name])
                and str(row[name]).strip()
            ),
            f"master-row-{int(index)}",
        )
        values.append(selected)
    return pd.Series(values, index=master.index, dtype="string")


def _record_missing(
    frame: pd.DataFrame,
    column: str,
    reason: str,
    output: dict[int, list[str]],
) -> None:
    for index in frame.index[frame[column].isna()]:
        output[int(index)].append(reason)


def _record_nonfinite(
    frame: pd.DataFrame,
    column: str,
    reason: str,
    output: dict[int, list[str]],
) -> None:
    values = pd.to_numeric(frame[column], errors="coerce")
    mask = ~np.isfinite(values.to_numpy(dtype=float, na_value=np.nan))
    for index in frame.index[mask]:
        output[int(index)].append(reason)


def _blocker(dataset: str, row: pd.Series, reason: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "source_row_number": int(row["source_row_number"]),
        "trade_id": str(row["trade_id"]) if pd.notna(row["trade_id"]) else None,
        "reason": reason,
    }


def _finite(series: pd.Series) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    return values[np.isfinite(values)]
