"""Research-only point-in-time 5m feature rematerialization and challenger smoke."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from smartcrypto.learning.feature_contracts import build_feature_contract

SCHEMA_VERSION = "market_features_rematerialization_research_v2"
DECISION = "MANTER_EM_RESEARCH"
TIMEFRAME = "5m"
TIMEFRAME_SECONDS = 300
CHALLENGER_MIN_ROWS = 40
CHALLENGER_TEST_FRACTION = 0.25
CHALLENGER_EMBARGO_SECONDS = 300
RANDOM_SEED = 42

FEATURE_COLUMNS = (
    "feature_5m_ret_1",
    "feature_5m_ret_3",
    "feature_5m_range_pct",
    "feature_5m_body_pct",
    "feature_5m_volume_rel_12",
    "feature_5m_ema_gap_12",
)

SAFETY_FLAGS = {
    "research_only": True,
    "paper_only": True,
    "shadow_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "writes_runtime": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "writes_active_registry": False,
    "writes_signal_file": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "qlib_training_performed": False,
    "p08_allowed": False,
}


def build_market_features_rematerialization_research_v2(
    trades: pd.DataFrame,
    candles: pd.DataFrame,
    *,
    run_challenger: bool = False,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a no-write research report from in-memory trade and 5m candle rows."""

    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    report = _base_report(generated_at=generated_at, run_challenger=run_challenger)
    try:
        normalized_trades = normalize_trades(trades)
        rematerialized = rematerialize_5m_features(candles)
        aligned = align_point_in_time_features(normalized_trades, rematerialized)
    except (KeyError, TypeError, ValueError) as exc:
        report.update(
            status="blocked",
            reason="input_validation_or_rematerialization_failed",
            validation_errors=[f"{type(exc).__name__}:{exc}"],
        )
        return report

    ready = aligned.loc[aligned["row_status"].eq("ready")].copy()
    contract_frame = ready.loc[:, list(FEATURE_COLUMNS)].copy()
    contract_frame["label_profitable"] = ready["label_profitable"].astype("Int64")
    feature_contract = build_feature_contract(
        contract_frame,
        source_datasets=["in_memory_trades", "in_memory_5m_candles"],
    )
    drift = build_temporal_drift_diagnostics(ready)
    challenger = (
        evaluate_ephemeral_challenger(ready)
        if run_challenger
        else _not_requested_challenger()
    )

    blocker_counts = _blocker_counts(aligned)
    validation_errors: list[str] = []
    if feature_contract["validation_status"] != "ok":
        validation_errors.extend(str(item) for item in feature_contract["validation_errors"])
    if ready.empty:
        validation_errors.append("no_point_in_time_rows_ready")

    status, reason = _final_status(
        validation_errors=validation_errors,
        challenger=challenger,
        run_challenger=run_challenger,
    )
    report.update(
        status=status,
        reason=reason,
        input_trade_row_count=int(len(normalized_trades)),
        input_candle_row_count=int(len(candles)),
        normalized_5m_candle_row_count=int(len(rematerialized)),
        ready_row_count=int(len(ready)),
        blocked_row_count=int(len(aligned) - len(ready)),
        blocker_reason_counts=blocker_counts,
        point_in_time_contract={
            "timeframe": TIMEFRAME,
            "timestamp_semantics": "candle_open",
            "available_at_rule": "candle_timestamp_utc_plus_5_minutes",
            "join_rule": "latest_available_at_lte_trade_open",
            "maximum_feature_age_seconds_exclusive": TIMEFRAME_SECONDS,
            "forward_fill_across_gaps": False,
            "imputation_performed": False,
            "same_candle_lookahead_allowed": False,
        },
        feature_columns=list(FEATURE_COLUMNS),
        feature_contract=feature_contract,
        dataset_lineage={
            "lineage_type": "point_in_time_market_features_research_v2",
            "trade_frame_hash": _frame_hash(normalized_trades),
            "rematerialized_candle_frame_hash": _frame_hash(rematerialized),
            "aligned_ready_frame_hash": _frame_hash(ready),
            "source_rows_read_only": True,
            "dataset_write_performed": False,
        },
        drift_diagnostics=drift,
        ephemeral_challenger=challenger,
        validation_errors=sorted(set(validation_errors)),
    )
    return report


def normalize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Normalize trade identity, symbol, entry time and optional outcome label."""

    if not isinstance(trades, pd.DataFrame):
        raise TypeError("trades_must_be_dataframe")
    if trades.empty:
        raise ValueError("trades_empty")
    symbol_column = _first_existing(trades, ("symbol", "symbol_norm", "moeda"))
    time_column = _first_existing(
        trades,
        ("open_time_utc", "open_time", "entry_time", "horario_abertura"),
    )
    if symbol_column is None:
        raise ValueError("trade_symbol_column_missing")
    if time_column is None:
        raise ValueError("trade_open_time_column_missing")

    frame = pd.DataFrame(index=trades.index)
    frame["source_row_number"] = np.arange(len(trades), dtype=int)
    id_column = _first_existing(trades, ("trade_id", "internal_order_id", "order_id"))
    if id_column is None:
        frame["trade_id"] = frame["source_row_number"].map(lambda value: f"trade_{value}")
    else:
        frame["trade_id"] = trades[id_column].astype("string").fillna("").astype(str)
        missing_id = frame["trade_id"].str.strip().eq("")
        frame.loc[missing_id, "trade_id"] = frame.loc[missing_id, "source_row_number"].map(
            lambda value: f"trade_{value}"
        )

    frame["symbol"] = trades[symbol_column].map(_normalize_symbol)
    frame["open_time_utc"] = _utc_series(trades[time_column])
    pnl_column = _first_existing(trades, ("net_pnl", "pnl_fechado", "gross_pnl"))
    if pnl_column is None:
        frame["net_pnl_diagnostic"] = np.nan
    else:
        frame["net_pnl_diagnostic"] = pd.to_numeric(trades[pnl_column], errors="coerce")
    frame["label_profitable"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    valid_pnl = frame["net_pnl_diagnostic"].notna()
    frame.loc[valid_pnl, "label_profitable"] = (
        frame.loc[valid_pnl, "net_pnl_diagnostic"].gt(0).astype(int).astype("Int64")
    )
    reasons: list[tuple[str, ...]] = []
    for row in frame.itertuples(index=False):
        row_reasons: list[str] = []
        if row.symbol is None:
            row_reasons.append("invalid_symbol")
        if pd.isna(row.open_time_utc):
            row_reasons.append("invalid_open_time")
        reasons.append(tuple(row_reasons))
    frame["validation_block_reasons"] = reasons
    frame["row_status"] = frame["validation_block_reasons"].map(
        lambda values: "blocked" if values else "eligible_for_alignment"
    )
    return frame.reset_index(drop=True)


def rematerialize_5m_features(candles: pd.DataFrame) -> pd.DataFrame:
    """Rematerialize deterministic features inside contiguous 5m segments only."""

    if not isinstance(candles, pd.DataFrame):
        raise TypeError("candles_must_be_dataframe")
    if candles.empty:
        raise ValueError("candles_empty")
    symbol_column = _first_existing(candles, ("symbol", "symbol_norm"))
    timestamp_column = _first_existing(candles, ("ts", "timestamp", "open_time"))
    if symbol_column is None:
        raise ValueError("candle_symbol_column_missing")
    if timestamp_column is None:
        raise ValueError("candle_timestamp_column_missing")
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(candles.columns))
    if missing:
        raise ValueError("candle_ohlcv_columns_missing:" + ",".join(missing))

    if "tf" in candles.columns:
        timeframe = candles["tf"].astype(str).str.casefold()
        candles = candles.loc[timeframe.eq(TIMEFRAME)].copy()
        if candles.empty:
            raise ValueError("no_5m_candles")

    frame = pd.DataFrame(index=candles.index)
    frame["symbol"] = candles[symbol_column].map(_normalize_symbol)
    frame["candle_timestamp_utc"] = _utc_series(candles[timestamp_column])
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(candles[column], errors="coerce")
    frame = frame.dropna(
        subset=["symbol", "candle_timestamp_utc", "open", "high", "low", "close", "volume"]
    )
    if frame.empty:
        raise ValueError("no_valid_5m_candles")
    valid_ohlc = (
        frame["high"].ge(frame[["open", "close", "low"]].max(axis=1))
        & frame["low"].le(frame[["open", "close", "high"]].min(axis=1))
        & frame["open"].gt(0)
        & frame["close"].gt(0)
        & frame["volume"].ge(0)
    )
    frame = frame.loc[valid_ohlc].sort_values(
        ["symbol", "candle_timestamp_utc"], kind="mergesort"
    )
    frame = frame.drop_duplicates(["symbol", "candle_timestamp_utc"], keep="last")
    frame["contiguous_segment_id"] = frame.groupby("symbol", sort=False)[
        "candle_timestamp_utc"
    ].transform(
        lambda values: values.diff().ne(pd.Timedelta(seconds=TIMEFRAME_SECONDS)).cumsum()
    )

    materialized = [
        _rematerialize_segment(segment.copy())
        for _, segment in frame.groupby(
            ["symbol", "contiguous_segment_id"], sort=False, dropna=False
        )
    ]
    output = pd.concat(materialized, ignore_index=True)
    output["available_at_utc"] = output["candle_timestamp_utc"] + pd.Timedelta(
        seconds=TIMEFRAME_SECONDS
    )
    return output


def align_point_in_time_features(
    trades: pd.DataFrame,
    features: pd.DataFrame,
) -> pd.DataFrame:
    """Attach only 5m rows that were fully available at trade entry time."""

    output = trades.copy()
    output["feature_timestamp_utc"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["feature_available_at_utc"] = pd.Series(
        pd.NaT, index=output.index, dtype="datetime64[ns, UTC]"
    )
    output["feature_age_seconds"] = np.nan
    for column in FEATURE_COLUMNS:
        output[column] = np.nan

    eligible = output.index[output["row_status"].eq("eligible_for_alignment")]
    for symbol in sorted(output.loc[eligible, "symbol"].dropna().unique()):
        left_indices = output.index[output.index.isin(eligible) & output["symbol"].eq(symbol)]
        left = output.loc[left_indices, ["open_time_utc"]].copy()
        left["source_index"] = left.index
        right = features.loc[features["symbol"].eq(symbol)].copy()
        if right.empty:
            continue
        merged = pd.merge_asof(
            left.sort_values("open_time_utc"),
            right.sort_values("available_at_utc"),
            left_on="open_time_utc",
            right_on="available_at_utc",
            direction="backward",
            allow_exact_matches=True,
        )
        for row in merged.itertuples(index=False):
            index = int(row.source_index)
            output.at[index, "feature_timestamp_utc"] = row.candle_timestamp_utc
            output.at[index, "feature_available_at_utc"] = row.available_at_utc
            if pd.notna(row.available_at_utc):
                output.at[index, "feature_age_seconds"] = float(
                    (row.open_time_utc - row.available_at_utc).total_seconds()
                )
            for column in FEATURE_COLUMNS:
                output.at[index, column] = getattr(row, column)

    for index in eligible:
        reasons = list(output.at[index, "validation_block_reasons"])
        available_at = output.at[index, "feature_available_at_utc"]
        if pd.isna(available_at):
            reasons.append("missing_closed_5m_feature")
        else:
            age = float(output.at[index, "feature_age_seconds"])
            if age < 0:
                reasons.append("feature_not_available_before_entry")
            if age >= TIMEFRAME_SECONDS:
                reasons.append("five_minute_candle_gap_no_forward_fill")
        if any(not np.isfinite(float(output.at[index, column])) for column in FEATURE_COLUMNS):
            reasons.append("missing_numeric_5m_features_no_imputation")
        output.at[index, "validation_block_reasons"] = tuple(sorted(set(reasons)))
        output.at[index, "row_status"] = "blocked" if reasons else "ready"
    return output


def build_temporal_drift_diagnostics(ready: pd.DataFrame) -> dict[str, Any]:
    """Compare early/late ready cohorts without using outcomes as model features."""

    if len(ready) < 8:
        return {
            "status": "warning",
            "reason": "insufficient_rows_for_temporal_drift",
            "row_count": int(len(ready)),
            "outcome_fields_used_as_features": False,
            "feature_metrics": [],
        }
    ordered = ready.sort_values("open_time_utc", kind="mergesort").reset_index(drop=True)
    midpoint = len(ordered) // 2
    reference = ordered.iloc[:midpoint]
    target = ordered.iloc[midpoint:]
    metrics: list[dict[str, Any]] = []
    for column in FEATURE_COLUMNS:
        left = pd.to_numeric(reference[column], errors="coerce").dropna().astype(float)
        right = pd.to_numeric(target[column], errors="coerce").dropna().astype(float)
        left_mean = float(left.mean()) if not left.empty else None
        right_mean = float(right.mean()) if not right.empty else None
        left_std = float(left.std(ddof=0)) if not left.empty else None
        standardized_delta = None
        if left_mean is not None and right_mean is not None and left_std not in {None, 0.0}:
            standardized_delta = float((right_mean - left_mean) / left_std)
        metrics.append(
            {
                "feature": column,
                "reference_count": int(len(left)),
                "target_count": int(len(right)),
                "reference_mean": left_mean,
                "target_mean": right_mean,
                "reference_std": left_std,
                "standardized_mean_delta": standardized_delta,
            }
        )
    return {
        "status": "ok",
        "reason": "temporal_feature_drift_diagnostics_completed",
        "row_count": int(len(ordered)),
        "reference_row_count": int(len(reference)),
        "target_row_count": int(len(target)),
        "outcome_fields_used_as_features": False,
        "feature_metrics": metrics,
    }


def evaluate_ephemeral_challenger(ready: pd.DataFrame) -> dict[str, Any]:
    """Fit one ephemeral sklearn challenger using a temporal split and embargo."""

    labeled = ready.loc[ready["label_profitable"].notna()].copy()
    labeled = labeled.sort_values("open_time_utc", kind="mergesort").reset_index(drop=True)
    if len(labeled) < CHALLENGER_MIN_ROWS:
        return _blocked_challenger("insufficient_labeled_rows", len(labeled))
    split_index = max(1, int(len(labeled) * (1.0 - CHALLENGER_TEST_FRACTION)))
    if split_index >= len(labeled):
        return _blocked_challenger("temporal_split_unavailable", len(labeled))
    test_start = pd.Timestamp(labeled.iloc[split_index]["open_time_utc"])
    embargo_start = test_start - pd.Timedelta(seconds=CHALLENGER_EMBARGO_SECONDS)
    train = labeled.loc[labeled["open_time_utc"].lt(embargo_start)].copy()
    test = labeled.loc[labeled["open_time_utc"].ge(test_start)].copy()
    if len(train) < 20 or len(test) < 10:
        return _blocked_challenger("insufficient_rows_after_embargo", len(labeled))

    y_train = train["label_profitable"].astype(int)
    y_test = test["label_profitable"].astype(int)
    if y_train.nunique() < 2:
        return _blocked_challenger("train_class_diversity_not_met", len(labeled))
    if y_test.nunique() < 2:
        return _blocked_challenger("test_class_diversity_not_met", len(labeled))

    x_train = train.loc[:, list(FEATURE_COLUMNS)].astype(float)
    x_test = test.loc[:, list(FEATURE_COLUMNS)].astype(float)
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=1000, random_state=RANDOM_SEED),
    )
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1]
    majority_class = int(y_train.mean() >= 0.5)
    baseline_predictions = np.full(len(y_test), majority_class, dtype=int)
    accuracy = float(accuracy_score(y_test, predictions))
    baseline_accuracy = float(accuracy_score(y_test, baseline_predictions))
    roc_auc = float(roc_auc_score(y_test, probabilities))
    return {
        "status": "ok",
        "reason": "ephemeral_logistic_challenger_evaluated",
        "model_family": "logistic_regression",
        "training_row_count": int(len(train)),
        "test_row_count": int(len(test)),
        "embargo_seconds": CHALLENGER_EMBARGO_SECONDS,
        "temporal_split": True,
        "accuracy": accuracy,
        "majority_baseline_accuracy": baseline_accuracy,
        "accuracy_delta_vs_majority": float(accuracy - baseline_accuracy),
        "roc_auc": roc_auc,
        "paper_rows_used_for_fit": 0,
        "model_artifact_written": False,
        "registry_write_performed": False,
        "promotion_eligible": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
    }


def write_research_report(
    report: dict[str, Any],
    *,
    project_root: str | Path,
    output_path: str | Path = "data/reports/market_features_rematerialization_research_v2.json",
) -> Path:
    """Write only one JSON research report under data/reports."""

    root = Path(project_root).expanduser().resolve()
    target = Path(output_path)
    target = target if target.is_absolute() else root / target
    target = target.resolve()
    allowed = (root / "data" / "reports").resolve()
    try:
        target.relative_to(allowed)
    except ValueError as exc:
        raise ValueError("report_output_must_be_under_data_reports") from exc
    if target.suffix.lower() != ".json":
        raise ValueError("report_output_must_use_json_suffix")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["write_requested"] = True
    payload["write_performed"] = True
    payload["written_report_path"] = str(target)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_safe)
        + "\n",
        encoding="utf-8",
    )
    return target


def _rematerialize_segment(segment: pd.DataFrame) -> pd.DataFrame:
    close = segment["close"].astype(float)
    open_price = segment["open"].astype(float)
    high = segment["high"].astype(float)
    low = segment["low"].astype(float)
    volume = segment["volume"].astype(float)
    segment["feature_5m_ret_1"] = close.pct_change(1, fill_method=None)
    segment["feature_5m_ret_3"] = close.pct_change(3, fill_method=None)
    segment["feature_5m_range_pct"] = high.sub(low).div(close)
    segment["feature_5m_body_pct"] = close.sub(open_price).div(open_price)
    volume_mean = volume.rolling(12, min_periods=12).mean()
    segment["feature_5m_volume_rel_12"] = volume.div(volume_mean)
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    segment["feature_5m_ema_gap_12"] = close.div(ema_12).sub(1.0)
    return segment


def _base_report(*, generated_at: str, run_challenger: bool) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "reason": "not_evaluated",
        "decision": DECISION,
        "generated_at_utc": generated_at,
        "run_challenger_requested": bool(run_challenger),
        "write_requested": False,
        "write_performed": False,
        "qlib_security_gate_remains_blocked": True,
        "qlib_security_gate_bypassed": False,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def _final_status(
    *,
    validation_errors: list[str],
    challenger: dict[str, Any],
    run_challenger: bool,
) -> tuple[str, str]:
    if validation_errors:
        return "blocked", "point_in_time_research_contract_blocked"
    if run_challenger and challenger["status"] != "ok":
        return "warning", "rematerialization_ok_challenger_controlled_block"
    return "ok", "point_in_time_research_diagnostics_completed"


def _not_requested_challenger() -> dict[str, Any]:
    return {
        "status": "not_requested",
        "reason": "explicit_flag_required",
        "model_artifact_written": False,
        "registry_write_performed": False,
        "promotion_eligible": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
    }


def _blocked_challenger(reason: str, labeled_rows: int) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "labeled_row_count": int(labeled_rows),
        "model_artifact_written": False,
        "registry_write_performed": False,
        "promotion_eligible": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
    }


def _blocker_counts(frame: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reasons in frame["validation_block_reasons"]:
        for reason in reasons:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items()))


def _normalize_symbol(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    for token in ("/", "_", "-", ":USDT"):
        text = text.replace(token, "")
    return text if text in {"BTCUSDT", "ETHUSDT"} else None


def _first_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((column for column in candidates if column in frame.columns), None)


def _utc_series(values: pd.Series) -> pd.Series:
    try:
        return pd.to_datetime(values, utc=True, errors="coerce", format="mixed")
    except TypeError:
        return pd.to_datetime(values, utc=True, errors="coerce")


def _frame_hash(frame: pd.DataFrame) -> str:
    columns = sorted(str(column) for column in frame.columns)
    records: list[dict[str, Any]] = []
    for row in frame.loc[:, columns].to_dict(orient="records"):
        records.append({key: _json_safe(value) for key, value in row.items()})
    payload = {"columns": columns, "records": records}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, tuple):
        return list(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value
