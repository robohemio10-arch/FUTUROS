"""Frozen WQ4 regime/concentration analysis for the official post-OCR master.

Research/paper/shadow only. The module implements the externally frozen WQ4
method without operational authority and never changes RiskManager, Freqtrade,
models, the official master, SQLite, or exchange state.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import tempfile
from collections import deque
from pathlib import Path
from typing import Any, Mapping, NoReturn

import numpy as np
import pandas as pd

from smartcrypto.data.indicators import atr

from .contracts import OFFICIAL_MASTER_SHA256
from .loader import OfficialMasterData, sha256_file
from .qlib_dataset import (
    QlibDatasetValidationError,
    build_pit_feature_dataset,
)
from .segment_persistence import (
    OOS_PERIODS,
    WQ3_METHOD_HASH,
    build_segment_persistence_report,
    report_content_sha256 as wq3_report_content_sha256,
)
from .walkforward import (
    WQ2_METHOD_HASH,
    build_walkforward_report,
    report_content_sha256 as wq2_report_content_sha256,
)


SCHEMA_VERSION = "official_trades_master_regime_concentration_v1"
WQ4_METHOD_HASH = "e837eda4f95adbf4bc725ac7113b56a7562917eb912cc98442b2cbebf96dd6a0"
METHOD_FREEZE_V2_FILE_SHA256 = "62bf92f8eb66f35e43844bb81a9430099d30e121ca89c22b189646a4d3aaf0ec"
EXPECTED_WQ2_REPORT_SHA256 = "ca057649b970a781a20915bb2e34f61fe291721c5a985c805a91396196d3c51b"
EXPECTED_WQ3_REPORT_SHA256 = "09ff15c33435b3bd19ea16fa32c19e42ea2b4ff701259b356f13ec453edf3e93"
EXPECTED_PRIMARY_OOS_ROWS = 3066

REPORT_JSON = "OFFICIAL_TRADES_MASTER_REGIME_CONCENTRATION_V1.json"
REPORT_MD = "OFFICIAL_TRADES_MASTER_REGIME_CONCENTRATION_V1.md"
REGIMES_CSV = "OFFICIAL_TRADES_MASTER_REGIME_CONCENTRATION_REGIMES_V1.csv"
LOPO_CSV = "OFFICIAL_TRADES_MASTER_REGIME_CONCENTRATION_LOPO_V1.csv"
MANIFEST_CSV = "OFFICIAL_TRADES_MASTER_REGIME_CONCENTRATION_SHA256_MANIFEST_V1.csv"

SAFETY_FLAGS: dict[str, bool] = {
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

CODE_SOURCE_PATHS = {
    "wq6_pit_dataset": "smartcrypto/research/trades_master_official/qlib_dataset.py",
    "institutional_indicators": "smartcrypto/data/indicators.py",
    "feature_builder": "smartcrypto/data/feature_builder.py",
    "wq3_segment_persistence": (
        "smartcrypto/research/trades_master_official/segment_persistence.py"
    ),
    "research_monte_carlo": "smartcrypto/research/monte_carlo_risk.py",
    "risk_ruin_gate": ("smartcrypto/risk/monte_carlo_risk_ruin_stress_gate/gate.py"),
}


class OfficialRegimeConcentrationError(ValueError):
    """Fail-closed WQ4 validation error."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = details


def _fail(
    code: str,
    **details: Any,
) -> NoReturn:
    raise OfficialRegimeConcentrationError(
        code,
        **details,
    )


def _stable_hash(payload: Any) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    text = text.replace("_", "").replace("/", "").replace(":", "").replace("-", "")
    if text not in {"BTCUSDT", "ETHUSDT"}:
        _fail("unsupported_market_symbol", value=str(value))
    return text


def _validate_method_freeze(
    path: Path,
    *,
    project_root: Path,
    market_paths: Mapping[str, Path],
) -> dict[str, Any]:
    if not path.is_file():
        _fail("method_freeze_v2_missing", path=str(path))

    actual_file_sha = sha256_file(path)
    if actual_file_sha != METHOD_FREEZE_V2_FILE_SHA256:
        _fail(
            "method_freeze_v2_file_sha256_mismatch",
            expected=METHOD_FREEZE_V2_FILE_SHA256,
            actual=actual_file_sha,
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialRegimeConcentrationError("method_freeze_v2_invalid_json") from exc

    if not isinstance(payload, dict):
        _fail("method_freeze_v2_not_object")

    if payload.get("method_hash") != WQ4_METHOD_HASH:
        _fail(
            "method_freeze_v2_method_hash_mismatch",
            expected=WQ4_METHOD_HASH,
            actual=payload.get("method_hash"),
        )

    lineage = payload.get("source_lineage")
    if not isinstance(lineage, Mapping):
        _fail("method_freeze_v2_lineage_missing")

    if lineage.get("official_master_sha256") != OFFICIAL_MASTER_SHA256:
        _fail("method_freeze_v2_master_sha256_mismatch")

    expected_market = lineage.get("market_file_sha256")
    if not isinstance(expected_market, Mapping):
        _fail("method_freeze_v2_market_hashes_missing")

    for source_id, market_path in market_paths.items():
        expected = expected_market.get(source_id)
        actual = sha256_file(market_path) if market_path.is_file() else None
        if actual != expected:
            _fail(
                "market_source_sha256_mismatch",
                source_id=source_id,
                expected=expected,
                actual=actual,
                path=str(market_path),
            )

    expected_code = lineage.get("versioned_code_sha256")
    if not isinstance(expected_code, Mapping):
        _fail("method_freeze_v2_code_hashes_missing")

    for source_id, relative_path in CODE_SOURCE_PATHS.items():
        path_value = project_root / relative_path
        actual = sha256_file(path_value) if path_value.is_file() else None
        expected = expected_code.get(source_id)
        if actual != expected:
            _fail(
                "frozen_code_source_sha256_mismatch",
                source_id=source_id,
                expected=expected,
                actual=actual,
                path=str(path_value),
            )

    return payload


def _load_market_frame(
    path: Path,
    *,
    symbol: str,
    source_rank: int,
) -> pd.DataFrame:
    if not path.is_file():
        _fail("market_source_missing", path=str(path))

    try:
        raw = pd.read_parquet(path)
    except (OSError, ValueError, ImportError) as exc:
        raise OfficialRegimeConcentrationError(
            "market_source_unreadable",
            path=str(path),
        ) from exc

    timestamp_column = next(
        (
            column
            for column in ("timestamp", "ts", "open_time", "open_time_utc")
            if column in raw.columns
        ),
        None,
    )
    if timestamp_column is None:
        _fail("market_timestamp_column_missing", path=str(path))

    required = ("open", "high", "low", "close", "volume")
    missing = [column for column in required if column not in raw.columns]
    if missing:
        _fail("market_ohlcv_columns_missing", path=str(path), columns=missing)

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                raw[timestamp_column],
                utc=True,
                errors="coerce",
            ),
            **{column: pd.to_numeric(raw[column], errors="coerce") for column in required},
        }
    )

    if "symbol" in raw.columns:
        symbols = {_normalize_symbol(value) for value in raw["symbol"].dropna().unique().tolist()}
        if symbols and symbols != {symbol}:
            _fail(
                "market_symbol_mismatch",
                expected=symbol,
                actual=sorted(symbols),
                path=str(path),
            )

    if frame.isna().any().any():
        _fail("market_invalid_value", path=str(path))

    invalid = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
        | (frame["volume"] < 0.0)
    )
    if invalid.any():
        _fail(
            "market_invalid_ohlcv",
            path=str(path),
            count=int(invalid.sum()),
        )

    frame["symbol"] = symbol
    frame["_source_rank"] = int(source_rank)
    return frame


def _combine_market_sources(
    *,
    symbol: str,
    existing_path: Path,
    backfill_path: Path,
) -> pd.DataFrame:
    combined = pd.concat(
        [
            _load_market_frame(
                existing_path,
                symbol=symbol,
                source_rank=0,
            ),
            _load_market_frame(
                backfill_path,
                symbol=symbol,
                source_rank=1,
            ),
        ],
        ignore_index=True,
    )

    combined = (
        combined.sort_values(
            ["timestamp", "_source_rank"],
            kind="mergesort",
        )
        .drop_duplicates(
            subset=["timestamp"],
            keep="last",
        )
        .sort_values("timestamp", kind="mergesort")
        .reset_index(drop=True)
    )

    if combined["timestamp"].duplicated().any():
        _fail("combined_market_duplicate_timestamp", symbol=symbol)

    deltas = combined["timestamp"].diff().dropna()
    if not deltas.eq(pd.Timedelta(minutes=1)).all():
        _fail(
            "combined_market_not_minute_contiguous",
            symbol=symbol,
            gap_count=int((~deltas.eq(pd.Timedelta(minutes=1))).sum()),
        )

    return combined


def _rolling_current_percentile_rank(
    values: pd.Series,
    timestamps: pd.Series,
) -> np.ndarray:
    numeric = pd.to_numeric(
        values,
        errors="coerce",
    ).to_numpy(dtype=float)

    times = pd.to_datetime(
        timestamps,
        utc=True,
        errors="coerce",
    )

    result: np.ndarray[Any, np.dtype[np.float64]] = np.full(
        len(numeric),
        np.nan,
        dtype=float,
    )

    ordered: list[float] = []
    window: deque[float | None] = deque()
    previous: pd.Timestamp | None = None

    for index, (
        raw_value,
        raw_time,
    ) in enumerate(
        zip(
            numeric,
            times,
            strict=True,
        )
    ):
        timestamp = pd.Timestamp(raw_time)

        if previous is not None and timestamp - previous != pd.Timedelta(minutes=1):
            ordered.clear()
            window.clear()

        previous = timestamp

        value = float(raw_value) if math.isfinite(float(raw_value)) else None

        if value is not None:
            bisect.insort(
                ordered,
                value,
            )

        window.append(value)

        if len(window) > 1440:
            expired = window.popleft()

            if expired is not None:
                position = bisect.bisect_left(
                    ordered,
                    expired,
                )

                if position >= len(ordered) or ordered[position] != expired:
                    _fail("rolling_rank_state_corruption")

                ordered.pop(position)

        if value is None or len(ordered) < 720:
            continue

        left = bisect.bisect_left(
            ordered,
            value,
        )

        right = bisect.bisect_right(
            ordered,
            value,
        )

        result[index] = (left + 0.5 * (right - left)) / len(ordered)

    return result


def _minute_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()

    output["ret_close_30m"] = output["close"] / output["close"].shift(30) - 1.0
    output["pre_entry_volatility_20"] = (
        output["close"].pct_change().rolling(20, min_periods=20).std(ddof=0)
    )
    output["atr_14"] = atr(
        output[["high", "low", "close"]],
        14,
    )
    output["atr_pct_14"] = output["atr_14"] / output["close"].replace(0.0, np.nan)
    output["volatility_percentile_rank"] = _rolling_current_percentile_rank(
        output["pre_entry_volatility_20"],
        output["timestamp"],
    )

    return output.set_index("timestamp", drop=False)


def _metrics(
    frame: pd.DataFrame,
    *,
    baseline_net_pnl: float,
) -> dict[str, Any]:
    pnl = frame["economic_net_pnl"].to_numpy(dtype=float)

    gross_profit = float(pnl[pnl > 0.0].sum()) if pnl.size else 0.0
    gross_loss = float(pnl[pnl < 0.0].sum()) if pnl.size else 0.0

    profit_factor: float | None
    if gross_loss < 0.0:
        profit_factor = float(gross_profit / abs(gross_loss))
    else:
        profit_factor = None

    equity = np.concatenate(([0.0], np.cumsum(pnl))) if pnl.size else np.asarray([0.0], dtype=float)
    peaks = np.maximum.accumulate(equity)

    return {
        "trade_count": int(pnl.size),
        "net_pnl": float(pnl.sum()) if pnl.size else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "expectancy": float(pnl.mean()) if pnl.size else 0.0,
        "win_rate": float(np.mean(pnl > 0.0)) if pnl.size else 0.0,
        "max_drawdown": float((peaks - equity).max()),
        "net_pnl_share": (
            float(pnl.sum() / baseline_net_pnl) if pnl.size and baseline_net_pnl != 0.0 else None
        ),
    }


def _profit_factor_gt_one(metrics: Mapping[str, Any]) -> bool:
    profit_factor = metrics.get("profit_factor")

    if profit_factor is not None:
        return float(profit_factor) > 1.0

    return (
        float(metrics.get("gross_profit") or 0.0) > 0.0
        and float(metrics.get("gross_loss") or 0.0) == 0.0
    )


def _classify_wq4(
    *,
    august_pass: bool,
    trimmed_pass: bool,
    positive_supported_regime_count: int,
) -> str:
    if not august_pass or not trimmed_pass:
        return "FRAGILE"

    if positive_supported_regime_count < 2:
        return "CONCENTRATED_RESEARCH_CANDIDATE"

    return "REGIME_ROBUST_RESEARCH_CANDIDATE"


def _rebuild_predecessor_lineage(
    master: OfficialMasterData,
    freeze: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    wq2 = build_walkforward_report(master)
    wq2["status"] = "ok"
    wq2["report_content_sha256"] = wq2_report_content_sha256(wq2)

    if wq2.get("method_hash") != WQ2_METHOD_HASH:
        _fail("wq2_method_hash_mismatch")

    if wq2.get("report_content_sha256") != EXPECTED_WQ2_REPORT_SHA256:
        _fail(
            "wq2_report_content_sha256_mismatch",
            actual=wq2.get("report_content_sha256"),
        )

    wq3 = build_segment_persistence_report(
        master,
        parent_wq2_report=wq2,
    )
    wq3["status"] = "ok"
    wq3["report_content_sha256"] = wq3_report_content_sha256(wq3)

    if wq3.get("method_hash") != WQ3_METHOD_HASH:
        _fail("wq3_method_hash_mismatch")

    if wq3.get("report_content_sha256") != EXPECTED_WQ3_REPORT_SHA256:
        _fail(
            "wq3_report_content_sha256_mismatch",
            actual=wq3.get("report_content_sha256"),
        )

    source_lineage = freeze.get("source_lineage")
    if not isinstance(source_lineage, Mapping):
        _fail("freeze_source_lineage_missing")

    checks = {
        "wq2_method_hash": WQ2_METHOD_HASH,
        "wq2_report_content_sha256": EXPECTED_WQ2_REPORT_SHA256,
        "wq3_method_hash": WQ3_METHOD_HASH,
        "wq3_report_content_sha256": EXPECTED_WQ3_REPORT_SHA256,
    }

    for key, expected in checks.items():
        if source_lineage.get(key) != expected:
            _fail(
                "freeze_predecessor_lineage_mismatch",
                field=key,
                expected=expected,
                actual=source_lineage.get(key),
            )

    return wq2, wq3


def build_labeled_oos_regime_frame(
    master: OfficialMasterData,
    *,
    project_root: str | Path,
    method_freeze_v2_path: str | Path,
    market_paths: Mapping[str, str | Path],
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Build the frozen WQ4 PIT-labelled primary OOS frame. This is the canonical shared regime-labelling implementation for downstream WQ5 research."""
    root = Path(project_root).resolve()
    freeze_path = Path(method_freeze_v2_path).resolve()
    required_market_ids = {
        "BTCUSDT_existing",
        "ETHUSDT_existing",
        "BTCUSDT_backfill",
        "ETHUSDT_backfill",
    }
    if set(market_paths) != required_market_ids:
        _fail(
            "market_path_ids_mismatch",
            expected=sorted(required_market_ids),
            actual=sorted(market_paths),
        )
    resolved_market_paths = {key: Path(value).resolve() for key, value in market_paths.items()}
    if master.audit.status != "ok":
        _fail("master_audit_not_ok", status=master.audit.status)
    if master.audit.master_sha256 != OFFICIAL_MASTER_SHA256:
        _fail("master_sha256_mismatch", actual=master.audit.master_sha256)
    if len(master.frame) != 3991:
        _fail("master_row_count_mismatch", expected=3991, actual=len(master.frame))
    freeze = _validate_method_freeze(
        freeze_path, project_root=root, market_paths=resolved_market_paths
    )
    wq2, wq3 = _rebuild_predecessor_lineage(master, freeze)
    combined = {
        "BTCUSDT": _combine_market_sources(
            symbol="BTCUSDT",
            existing_path=resolved_market_paths["BTCUSDT_existing"],
            backfill_path=resolved_market_paths["BTCUSDT_backfill"],
        ),
        "ETHUSDT": _combine_market_sources(
            symbol="ETHUSDT",
            existing_path=resolved_market_paths["ETHUSDT_existing"],
            backfill_path=resolved_market_paths["ETHUSDT_backfill"],
        ),
    }
    try:
        dataset, pit_evidence = build_pit_feature_dataset(
            master.frame,
            market_by_symbol={
                symbol: frame.set_index("timestamp", drop=False)
                for symbol, frame in combined.items()
            },
        )
    except QlibDatasetValidationError as exc:
        raise OfficialRegimeConcentrationError("pit_dataset_build_failed", error=str(exc)) from exc
    if (
        pit_evidence.get("pit_eligible_rows") != 3760
        or pit_evidence.get("legacy_excluded_rows") != 231
        or pit_evidence.get("pit_eligible_excluded_rows") != 0
    ):
        _fail("pit_dataset_coverage_mismatch", evidence=pit_evidence)
    dataset["period"] = pd.to_datetime(dataset["open_time_utc"], utc=True).dt.strftime("%Y-%m")
    oos = (
        dataset.loc[dataset["period"].isin(OOS_PERIODS)]
        .sort_values(["open_time_utc", "trade_sequence"], kind="mergesort")
        .reset_index(drop=True)
    )
    if len(oos) != EXPECTED_PRIMARY_OOS_ROWS:
        _fail("primary_oos_row_count_mismatch", expected=EXPECTED_PRIMARY_OOS_ROWS, actual=len(oos))
    minute = {symbol: _minute_features(frame) for symbol, frame in combined.items()}
    atr_values: list[float] = []
    rank_values: list[float] = []
    for row in oos.itertuples(index=False):
        anchor = pd.Timestamp(row.open_time_utc).floor("min") - pd.Timedelta(minutes=1)
        source = minute[str(row.symbol)]
        if anchor not in source.index:
            _fail("pit_regime_anchor_missing", trade_sequence=int(row.trade_sequence))
        point = source.loc[anchor]
        if isinstance(point, pd.DataFrame):
            _fail("pit_regime_anchor_not_unique", trade_sequence=int(row.trade_sequence))
        atr_pct = float(point["atr_pct_14"])
        vol_rank = float(point["volatility_percentile_rank"])
        ret30 = float(point["ret_close_30m"])
        vol20 = float(point["pre_entry_volatility_20"])
        values = (atr_pct, vol_rank, ret30, vol20)
        if not all((math.isfinite(value) for value in values)):
            _fail("pit_regime_feature_missing", trade_sequence=int(row.trade_sequence))
        if not math.isclose(ret30, float(row.feature_ret_close_30m), rel_tol=0.0, abs_tol=1e-12):
            _fail("wq6_ret30_semantic_drift", trade_sequence=int(row.trade_sequence))
        if not math.isclose(
            vol20, float(row.feature_pre_entry_volatility_20), rel_tol=0.0, abs_tol=1e-12
        ):
            _fail("wq6_vol20_semantic_drift", trade_sequence=int(row.trade_sequence))
        atr_values.append(atr_pct)
        rank_values.append(vol_rank)
    oos["atr_pct_14"] = atr_values
    oos["volatility_percentile_rank"] = rank_values
    oos["economic_net_pnl"] = oos["label_economic_net_pnl"].astype(float)
    denominator = np.maximum(
        oos["feature_pre_entry_volatility_20"].to_numpy(dtype=float) * math.sqrt(30.0), 1e-12
    )
    oos["trend_score"] = oos["feature_ret_close_30m"].to_numpy(dtype=float) / denominator
    oos["trend_state"] = np.select(
        [oos["trend_score"] >= 0.5, oos["trend_score"] <= -0.5], ["UP", "DOWN"], default="RANGE"
    )
    oos["volatility_state"] = np.select(
        [oos["volatility_percentile_rank"] <= 0.33, oos["volatility_percentile_rank"] >= 0.67],
        ["LOW", "HIGH"],
        default="NORMAL",
    )
    oos["composite_regime"] = (
        oos["trend_state"].astype(str) + "__" + oos["volatility_state"].astype(str)
    )
    lineage: dict[str, Any] = {
        "wq2_report_content_sha256": wq2["report_content_sha256"],
        "wq3_report_content_sha256": wq3["report_content_sha256"],
        "market_file_sha256": {
            key: sha256_file(path) for key, path in resolved_market_paths.items()
        },
    }
    return (oos, pit_evidence, lineage)


def build_regime_concentration_report(
    master: OfficialMasterData,
    *,
    project_root: str | Path,
    method_freeze_v2_path: str | Path,
    market_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    oos, pit_evidence, lineage = build_labeled_oos_regime_frame(
        master,
        project_root=project_root,
        method_freeze_v2_path=method_freeze_v2_path,
        market_paths=market_paths,
    )
    baseline_net_pnl = float(oos["economic_net_pnl"].sum())
    baseline = _metrics(oos, baseline_net_pnl=baseline_net_pnl)
    without_august = _metrics(
        oos.loc[oos["period"] != "2026-08"], baseline_net_pnl=baseline_net_pnl
    )
    august_pass = without_august["net_pnl"] > 0.0 and _profit_factor_gt_one(without_august)
    remove_count = max(1, int(math.ceil(len(oos) * 0.05)))
    ordered = oos.sort_values(
        ["economic_net_pnl", "trade_sequence"], ascending=[False, True], kind="mergesort"
    )
    removed_indices = set(ordered.head(remove_count).index.tolist())
    trimmed = oos.loc[~oos.index.isin(removed_indices)].copy()
    trimmed_metrics = _metrics(trimmed, baseline_net_pnl=baseline_net_pnl)
    trimmed_pass = trimmed_metrics["net_pnl"] > 0.0 and _profit_factor_gt_one(trimmed_metrics)
    leave_one_period_out = [
        {
            "excluded_period": period,
            **_metrics(oos.loc[oos["period"] != period], baseline_net_pnl=baseline_net_pnl),
        }
        for period in OOS_PERIODS
    ]
    regimes: list[dict[str, Any]] = []
    for regime, subset in oos.groupby("composite_regime", sort=True, observed=True):
        row = _metrics(subset, baseline_net_pnl=baseline_net_pnl)
        supported = row["trade_count"] >= 30
        positive_supported = supported and row["net_pnl"] > 0.0 and _profit_factor_gt_one(row)
        regimes.append(
            {
                "composite_regime": str(regime),
                **row,
                "supported": supported,
                "positive_supported": positive_supported,
            }
        )
    positive_supported_regime_count = sum((bool(row["positive_supported"]) for row in regimes))
    multi_regime_support_pass = positive_supported_regime_count >= 2
    classification = _classify_wq4(
        august_pass=august_pass,
        trimmed_pass=trimmed_pass,
        positive_supported_regime_count=positive_supported_regime_count,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "engineering_status": "PASS",
        "wq4_status": "REGIME_READY",
        "decision": "WQ4_REGIME_CONCENTRATION_EVALUATED",
        "quant_classification": classification,
        "master_sha256": master.audit.master_sha256,
        "method_v2_hash": WQ4_METHOD_HASH,
        "method_v2_file_sha256": METHOD_FREEZE_V2_FILE_SHA256,
        "wq2_method_hash": WQ2_METHOD_HASH,
        "wq2_report_content_sha256": lineage["wq2_report_content_sha256"],
        "wq3_method_hash": WQ3_METHOD_HASH,
        "wq3_report_content_sha256": lineage["wq3_report_content_sha256"],
        "primary_oos_rows": len(oos),
        "baseline": baseline,
        "without_august": {**without_august, "gate_pass": august_pass},
        "top_5_percent_removed": {
            **trimmed_metrics,
            "removed_trade_count": remove_count,
            "gate_pass": trimmed_pass,
        },
        "leave_one_period_out": leave_one_period_out,
        "regimes": regimes,
        "positive_supported_regime_count": positive_supported_regime_count,
        "multi_regime_support_pass": multi_regime_support_pass,
        "regime_labels": sorted(oos["composite_regime"].unique().tolist()),
        "pit_evidence": pit_evidence,
        "source_lineage": {"market_file_sha256": lineage["market_file_sha256"]},
        **SAFETY_FLAGS,
        "safety": dict(SAFETY_FLAGS),
    }


def report_content_sha256(payload: Mapping[str, Any]) -> str:
    normalized = dict(payload)
    normalized.pop("report_content_sha256", None)
    normalized.pop("report_paths", None)
    return _stable_hash(normalized)


def render_markdown(payload: Mapping[str, Any]) -> str:
    baseline = payload["baseline"]
    without_august = payload["without_august"]
    trimmed = payload["top_5_percent_removed"]

    lines = [
        "# Official Trades Master — WQ4 Regime / Concentration",
        "",
        f"- Status: `{payload['wq4_status']}`",
        f"- Classification: `{payload['quant_classification']}`",
        f"- OOS trades: `{payload['primary_oos_rows']}`",
        f"- Net PnL OOS: `{baseline['net_pnl']:.6f}`",
        f"- Profit Factor OOS: `{baseline['profit_factor']:.10f}`",
        f"- Without August Net PnL: `{without_august['net_pnl']:.6f}`",
        f"- Without August PF: `{without_august['profit_factor']:.10f}`",
        f"- Top 5% removed Net PnL: `{trimmed['net_pnl']:.6f}`",
        f"- Top 5% removed PF: `{trimmed['profit_factor']:.10f}`",
        (f"- Positive supported regimes: `{payload['positive_supported_regime_count']}`"),
        "",
        "## Regimes",
        "",
        "| Regime | Trades | Net PnL | PF | Expectancy | Win rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for row in payload["regimes"]:
        lines.append(
            "| {regime} | {trades} | {net:.6f} | {pf:.6f} | {exp:.6f} | {wr:.6f} |".format(
                regime=row["composite_regime"],
                trades=row["trade_count"],
                net=row["net_pnl"],
                pf=row["profit_factor"],
                exp=row["expectancy"],
                wr=row["win_rate"],
            )
        )

    lines.extend(
        [
            "",
            "Research-only. No operational authority, no RiskManager change, "
            "no live/canary/order authority.",
            "",
        ]
    )

    return "\n".join(lines)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    temporary = Path(temporary_name)

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, path)

    finally:
        temporary.unlink(missing_ok=True)


def persist_regime_concentration_reports(
    payload: Mapping[str, Any],
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    root = Path(output_dir).resolve()

    json_path = root / REPORT_JSON
    markdown_path = root / REPORT_MD
    regimes_path = root / REGIMES_CSV
    lopo_path = root / LOPO_CSV
    manifest_path = root / MANIFEST_CSV

    json_bytes = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    markdown_bytes = (render_markdown(payload)).encode("utf-8")

    regimes_bytes = (
        pd.DataFrame(payload["regimes"]).to_csv(index=False, lineterminator="\n").encode("utf-8")
    )

    lopo_bytes = (
        pd.DataFrame(payload["leave_one_period_out"])
        .to_csv(index=False, lineterminator="\n")
        .encode("utf-8")
    )

    for path, content in (
        (json_path, json_bytes),
        (markdown_path, markdown_bytes),
        (regimes_path, regimes_bytes),
        (lopo_path, lopo_bytes),
    ):
        _atomic_write(path, content)

    manifest_rows = []

    for path in (
        json_path,
        markdown_path,
        regimes_path,
        lopo_path,
    ):
        manifest_rows.append(
            {
                "file": path.name,
                "sha256": sha256_file(path),
            }
        )

    manifest_bytes = (
        pd.DataFrame(manifest_rows).to_csv(index=False, lineterminator="\n").encode("utf-8")
    )
    _atomic_write(manifest_path, manifest_bytes)

    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
        "regimes_csv": str(regimes_path),
        "lopo_csv": str(lopo_path),
        "sha256_manifest_csv": str(manifest_path),
    }
