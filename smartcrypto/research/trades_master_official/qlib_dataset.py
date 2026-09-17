"""Point-in-time Qlib research dataset from the official post-OCR trades master.

Research/paper/shadow only. This module never trains, promotes, trades, changes
risk, accesses private exchange APIs, or mutates the official trades master.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smartcrypto.learning.feature_contracts.dataset_manifest import frame_hash
from smartcrypto.learning.feature_contracts.feature_contract import (
    build_feature_contract,
    contract_hash,
    stable_hash,
)
from smartcrypto.learning.walkforward.leakage_audit import audit_leakage
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
    public_split,
)
from smartcrypto.research.trades_master_official.contracts import (
    OFFICIAL_MASTER_SHA256,
)
from smartcrypto.research.trades_master_official.loader import (
    load_official_trades_master,
    sha256_file,
)

SCHEMA_VERSION = "official_trades_master_qlib_dataset_v1"

EXPECTED_DESIGN_FREEZE_SHA256 = "6ecbbfc1ca73d6fde68b49bdfd06b8b1316114e0d0892e12836959e5f8172ea4"

EXPECTED_BACKFILL_VALIDATION_SHA256 = (
    "12093fd23e4d9907b6f75d715240341dccfee91ecd66ecb10be8becf159e535e"
)

EXPECTED_BACKFILL_SHA256 = {
    "BTCUSDT": "f94e8f91be2f7d57c8666b8d736bb73501c76cf06c45161b0568adc7b13db40c",
    "ETHUSDT": "5ff642cc98e8452565f7c3d61f25a9cf5603102aa639cf85e05e5008eb3e13e4",
}

EXPECTED_PIT_ELIGIBLE_ROWS = 3760
EXPECTED_LEGACY_ROWS = 231
EMBARGO_SECONDS = 86_400
MARKET_SOURCE_ID = "binance_usdt_m_futures_public_closed_1m"

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "training_requested": False,
    "training_performed": False,
    "qlib_training_performed": False,
    "model_promotion_performed": False,
    "active_model_changed": False,
    "changes_risk": False,
    "sends_orders": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "writes_master": False,
    "writes_sqlite": False,
}

FEATURE_FORMULAS: dict[str, str] = {
    "feature_side_long": "1 when canonical trade side is long, else 0",
    "feature_side_short": "1 when canonical trade side is short, else 0",
    "feature_symbol_btcusdt": "1 when normalized symbol is BTCUSDT, else 0",
    "feature_symbol_ethusdt": "1 when normalized symbol is ETHUSDT, else 0",
    "feature_open_hour_sin": "sin(2*pi*UTC_hour/24)",
    "feature_open_hour_cos": "cos(2*pi*UTC_hour/24)",
    "feature_open_dow_sin": "sin(2*pi*UTC_day_of_week/7)",
    "feature_open_dow_cos": "cos(2*pi*UTC_day_of_week/7)",
    "feature_ret_close_1m": "close[t]/close[t-1m]-1 using closed 1m candles",
    "feature_ret_close_5m": "close[t]/close[t-5m]-1 using closed 1m candles",
    "feature_ret_close_10m": "close[t]/close[t-10m]-1 using closed 1m candles",
    "feature_ret_close_30m": "close[t]/close[t-30m]-1 using closed 1m candles",
    "feature_high_low_range_pct_5m": "(max(high,last5)-min(low,last5))/first_open(last5)",
    "feature_high_low_range_pct_10m": "(max(high,last10)-min(low,last10))/first_open(last10)",
    "feature_high_low_range_pct_30m": "(max(high,last30)-min(low,last30))/first_open(last30)",
    "feature_volume_sum_5m": "sum(volume,last5 closed 1m candles)",
    "feature_volume_sum_10m": "sum(volume,last10 closed 1m candles)",
    "feature_volume_sum_30m": "sum(volume,last30 closed 1m candles)",
    "feature_dist_sma_20_pct": "latest_close/mean(last20 closes)-1",
    "feature_rsi_14": "simple 14-period RSI from 15 closed 1m closes",
    "feature_pre_entry_volatility_20": "population stddev of last20 one-minute close returns",
}


class QlibDatasetValidationError(ValueError):
    """Raised when the frozen WQ6 PIT dataset contract cannot be satisfied."""


@dataclass
class QlibDatasetArtifacts:
    dataset: pd.DataFrame
    feature_contract: dict[str, Any]
    dataset_manifest: dict[str, Any]
    target_store: dict[str, Any]
    split_manifest: dict[str, Any]
    report: dict[str, Any]


def _file_sha256(path: Path) -> str:
    return sha256_file(path)


def _load_json_contract(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise QlibDatasetValidationError(f"{label}_not_found")

    actual_sha = _file_sha256(path)

    if actual_sha != expected_sha256:
        raise QlibDatasetValidationError(
            f"{label}_sha256_mismatch:expected={expected_sha256}:actual={actual_sha}"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QlibDatasetValidationError(f"{label}_invalid_json") from exc

    if not isinstance(payload, dict):
        raise QlibDatasetValidationError(f"{label}_must_be_object")

    return payload


def _normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    text = text.replace("_", "").replace("/", "").replace(":", "").replace("-", "")
    if text not in {"BTCUSDT", "ETHUSDT"}:
        raise QlibDatasetValidationError(f"unsupported_symbol:{value}")
    return text


def _normalize_side(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "long": "long",
        "buy": "long",
        "short": "short",
        "sell": "short",
    }
    normalized = aliases.get(text)
    if normalized is None:
        raise QlibDatasetValidationError(f"unsupported_side:{value}")
    return normalized


def _normalize_market_frame(
    path: Path,
    *,
    expected_symbol: str,
    source_rank: int,
) -> pd.DataFrame:
    if not path.exists() or not path.is_file():
        raise QlibDatasetValidationError(f"market_file_not_found:{path}")

    try:
        raw = pd.read_parquet(path)
    except (OSError, ValueError, ImportError) as exc:
        raise QlibDatasetValidationError(f"market_file_unreadable:{path}") from exc

    timestamp_column = None
    for candidate in ("timestamp", "ts", "open_time", "open_time_utc"):
        if candidate in raw.columns:
            timestamp_column = candidate
            break

    if timestamp_column is None:
        raise QlibDatasetValidationError(f"market_timestamp_column_missing:{path}")

    required = ("open", "high", "low", "close", "volume")
    missing = [column for column in required if column not in raw.columns]
    if missing:
        raise QlibDatasetValidationError(f"market_ohlcv_columns_missing:{path}:{','.join(missing)}")

    timestamp = pd.to_datetime(
        raw[timestamp_column],
        utc=True,
        errors="coerce",
    )

    frame = pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw["volume"], errors="coerce"),
        }
    )

    if "symbol" in raw.columns:
        symbols = {_normalize_symbol(value) for value in raw["symbol"].dropna().unique().tolist()}
        if symbols and symbols != {expected_symbol}:
            raise QlibDatasetValidationError(f"market_symbol_mismatch:{path}:{sorted(symbols)}")

    if frame["timestamp"].isna().any():
        raise QlibDatasetValidationError(f"market_invalid_timestamp:{path}")

    if frame[list(required)].isna().any().any():
        raise QlibDatasetValidationError(f"market_invalid_numeric_value:{path}")

    invalid_ohlcv = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
        | (frame["volume"] < 0)
    )

    if invalid_ohlcv.any():
        raise QlibDatasetValidationError(f"market_invalid_ohlcv:{path}")

    frame["symbol"] = expected_symbol
    frame["_source_rank"] = int(source_rank)

    return frame


def _combine_market_sources(
    *,
    symbol: str,
    existing_path: Path,
    backfill_path: Path,
) -> pd.DataFrame:
    existing = _normalize_market_frame(
        existing_path,
        expected_symbol=symbol,
        source_rank=0,
    )
    backfill = _normalize_market_frame(
        backfill_path,
        expected_symbol=symbol,
        source_rank=1,
    )

    combined = pd.concat(
        [existing, backfill],
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
        raise QlibDatasetValidationError(f"combined_market_duplicate_timestamp:{symbol}")

    return combined.set_index("timestamp", drop=False)


def _rsi_14(closes: pd.Series) -> float:
    if len(closes) != 15:
        raise QlibDatasetValidationError("rsi_requires_15_closes")

    changes = closes.diff().dropna()
    gains = changes.clip(lower=0.0)
    losses = -changes.clip(upper=0.0)

    average_gain = float(gains.mean())
    average_loss = float(losses.mean())

    if average_loss == 0.0:
        if average_gain == 0.0:
            return 50.0
        return 100.0

    rs = average_gain / average_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _features_for_trade(
    *,
    market: pd.DataFrame,
    opened: pd.Timestamp,
    symbol: str,
    side: str,
) -> tuple[dict[str, float | int], pd.Timestamp]:
    anchor_open = opened.floor("min") - pd.Timedelta(minutes=1)

    expected_index = pd.date_range(
        anchor_open - pd.Timedelta(minutes=30),
        anchor_open,
        freq="1min",
        tz="UTC",
    )

    window = market.reindex(expected_index)

    if len(window) != 31:
        raise QlibDatasetValidationError("market_window_length_invalid")

    required = ["open", "high", "low", "close", "volume"]

    if window[required].isna().any().any():
        raise QlibDatasetValidationError("market_window_incomplete")

    closes = window["close"].astype(float)
    latest_close = float(closes.iloc[-1])

    if latest_close <= 0.0:
        raise QlibDatasetValidationError("latest_close_non_positive")

    feature_values: dict[str, float | int] = {
        "feature_side_long": int(side == "long"),
        "feature_side_short": int(side == "short"),
        "feature_symbol_btcusdt": int(symbol == "BTCUSDT"),
        "feature_symbol_ethusdt": int(symbol == "ETHUSDT"),
        "feature_open_hour_sin": math.sin(2.0 * math.pi * opened.hour / 24.0),
        "feature_open_hour_cos": math.cos(2.0 * math.pi * opened.hour / 24.0),
        "feature_open_dow_sin": math.sin(2.0 * math.pi * opened.dayofweek / 7.0),
        "feature_open_dow_cos": math.cos(2.0 * math.pi * opened.dayofweek / 7.0),
    }

    for minutes in (1, 5, 10, 30):
        denominator = float(closes.loc[anchor_open - pd.Timedelta(minutes=minutes)])
        if denominator <= 0.0:
            raise QlibDatasetValidationError(f"return_denominator_non_positive:{minutes}")
        feature_values[f"feature_ret_close_{minutes}m"] = latest_close / denominator - 1.0

    for minutes in (5, 10, 30):
        sub = window.loc[anchor_open - pd.Timedelta(minutes=minutes - 1) : anchor_open]

        first_open = float(sub["open"].iloc[0])
        if first_open <= 0.0:
            raise QlibDatasetValidationError(f"range_denominator_non_positive:{minutes}")

        feature_values[f"feature_high_low_range_pct_{minutes}m"] = (
            float(sub["high"].max()) - float(sub["low"].min())
        ) / first_open

        feature_values[f"feature_volume_sum_{minutes}m"] = float(sub["volume"].sum())

    sma_20 = float(closes.iloc[-20:].mean())
    if sma_20 <= 0.0:
        raise QlibDatasetValidationError("sma20_non_positive")

    feature_values["feature_dist_sma_20_pct"] = latest_close / sma_20 - 1.0

    feature_values["feature_rsi_14"] = _rsi_14(closes.iloc[-15:])

    returns_20 = closes.iloc[-21:].pct_change().dropna()

    feature_values["feature_pre_entry_volatility_20"] = float(returns_20.std(ddof=0))

    numeric_values = np.asarray(
        [float(value) for value in feature_values.values()],
        dtype=float,
    )

    if not np.isfinite(numeric_values).all():
        raise QlibDatasetValidationError("non_finite_feature_value")

    feature_cutoff_utc = anchor_open + pd.Timedelta(minutes=1)

    if feature_cutoff_utc > opened:
        raise QlibDatasetValidationError("feature_cutoff_after_trade_open")

    return feature_values, feature_cutoff_utc


def build_pit_feature_dataset(
    master_frame: pd.DataFrame,
    *,
    market_by_symbol: Mapping[str, pd.DataFrame],
    expected_eligible_rows: int | None = EXPECTED_PIT_ELIGIBLE_ROWS,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if "horario_abertura" not in master_frame.columns:
        raise QlibDatasetValidationError("master_missing_horario_abertura")

    opening = pd.to_datetime(
        master_frame["horario_abertura"],
        format="mixed",
        utc=True,
        errors="coerce",
    )

    closing = pd.to_datetime(
        master_frame["horario_fechamento"],
        format="mixed",
        utc=True,
        errors="coerce",
    )

    eligible_mask = opening.notna()
    eligible_rows = int(eligible_mask.sum())
    legacy_rows = int((~eligible_mask).sum())

    if expected_eligible_rows is not None:
        if eligible_rows != expected_eligible_rows:
            raise QlibDatasetValidationError(
                f"pit_eligible_row_count_mismatch:"
                f"expected={expected_eligible_rows}:actual={eligible_rows}"
            )

        expected_legacy = len(master_frame) - expected_eligible_rows
        if legacy_rows != expected_legacy:
            raise QlibDatasetValidationError(
                f"legacy_row_count_mismatch:expected={expected_legacy}:actual={legacy_rows}"
            )

    rows: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}

    for position in np.flatnonzero(eligible_mask.to_numpy()):
        source = master_frame.iloc[int(position)]

        try:
            opened = opening.iloc[int(position)]
            closed = closing.iloc[int(position)]

            if pd.isna(closed):
                raise QlibDatasetValidationError("invalid_close_time")

            symbol = _normalize_symbol(source["symbol"])
            side = _normalize_side(source["side"])

            market = market_by_symbol.get(symbol)
            if market is None:
                raise QlibDatasetValidationError(f"market_symbol_unavailable:{symbol}")

            features, cutoff = _features_for_trade(
                market=market,
                opened=opened,
                symbol=symbol,
                side=side,
            )

            pnl = float(source["economic_net_pnl"])

            if not math.isfinite(pnl):
                raise QlibDatasetValidationError("economic_net_pnl_non_finite")

            order_id_raw = str(source["order_id_raw"]).strip()

            row: dict[str, Any] = {
                "trade_sequence": int(source["trade_sequence"]),
                "order_id": order_id_raw,
                "order_id_raw": order_id_raw,
                "symbol": symbol,
                "side": side,
                "open_time_utc": opened,
                "close_time_utc": closed,
                "feature_cutoff_utc": cutoff,
                "market_source": MARKET_SOURCE_ID,
                **features,
                "label_economic_net_pnl": pnl,
                "label_is_profitable": int(pnl > 0.0),
            }
            rows.append(row)

        except (
            KeyError,
            TypeError,
            ValueError,
            QlibDatasetValidationError,
        ) as exc:
            reason = (
                exc.args[0]
                if isinstance(exc, QlibDatasetValidationError) and exc.args
                else type(exc).__name__
            )
            reason_text = str(reason)
            exclusion_counts[reason_text] = exclusion_counts.get(reason_text, 0) + 1

    dataset = pd.DataFrame(rows)

    if not dataset.empty:
        dataset = dataset.sort_values(
            ["open_time_utc", "trade_sequence"],
            kind="mergesort",
        ).reset_index(drop=True)

    evidence = {
        "master_rows": int(len(master_frame)),
        "pit_eligible_rows": eligible_rows,
        "legacy_excluded_rows": legacy_rows,
        "feature_complete_rows": int(len(dataset)),
        "pit_eligible_excluded_rows": int(eligible_rows - len(dataset)),
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
    }

    return dataset, evidence


def _ordered_names(
    design_freeze: Mapping[str, Any],
    section: str,
    key: str,
) -> list[str]:
    block = design_freeze.get(section)
    if not isinstance(block, Mapping):
        raise QlibDatasetValidationError(f"design_freeze_missing_section:{section}")

    records = block.get(key)
    if not isinstance(records, list):
        raise QlibDatasetValidationError(f"design_freeze_missing_list:{section}.{key}")

    names: list[str] = []

    for record in records:
        if not isinstance(record, Mapping):
            raise QlibDatasetValidationError(f"design_freeze_invalid_record:{section}.{key}")
        name = record.get("name")
        if not isinstance(name, str) or not name:
            raise QlibDatasetValidationError(f"design_freeze_invalid_name:{section}.{key}")
        names.append(name)

    return names


def _build_feature_contract(
    dataset: pd.DataFrame,
    *,
    design_freeze: Mapping[str, Any],
    source_datasets: list[str],
) -> dict[str, Any]:
    """Build the frozen WQ6 feature contract with explicit PIT metadata.

    The shared feature classifier intentionally treats ``feature_*`` as
    model features. WQ6, however, froze ``feature_cutoff_utc`` as lineage
    metadata, not as a model input. To preserve both contracts without
    changing shared classifier behavior, that single metadata field is
    excluded from shared role inference and restored explicitly afterward.
    """

    ordered_features = _ordered_names(
        design_freeze,
        "feature_contract",
        "ordered_features",
    )
    ordered_labels = _ordered_names(
        design_freeze,
        "label_contract",
        "ordered_labels",
    )

    if len(ordered_features) != len(set(ordered_features)):
        raise QlibDatasetValidationError("feature_contract_whitelist_drift")

    if len(ordered_labels) != len(set(ordered_labels)):
        raise QlibDatasetValidationError("label_contract_whitelist_drift")

    if "feature_cutoff_utc" not in dataset.columns:
        raise QlibDatasetValidationError("feature_cutoff_metadata_missing")

    metadata_contract = design_freeze.get("metadata_contract")

    if not isinstance(metadata_contract, Mapping):
        raise QlibDatasetValidationError("design_freeze_missing_section:metadata_contract")

    frozen_metadata = metadata_contract.get("columns")

    if not isinstance(frozen_metadata, list) or "feature_cutoff_utc" not in frozen_metadata:
        raise QlibDatasetValidationError("feature_cutoff_not_frozen_as_metadata")

    #
    # The shared classifier sees every ``feature_*`` column as a model
    # feature. Exclude the frozen metadata field only for shared inference.
    #
    contract_input = dataset.drop(columns=["feature_cutoff_utc"]).copy()

    contract = build_feature_contract(
        contract_input,
        source_datasets=source_datasets,
    )

    inferred_features = set(
        str(value)
        for value in contract.get(
            "feature_columns",
            [],
        )
    )
    inferred_labels = set(
        str(value)
        for value in contract.get(
            "label_columns",
            [],
        )
    )

    if inferred_features != set(ordered_features):
        raise QlibDatasetValidationError("feature_contract_whitelist_drift")

    if inferred_labels != set(ordered_labels):
        raise QlibDatasetValidationError("label_contract_whitelist_drift")

    roles = {
        str(key): str(value)
        for key, value in dict(
            contract.get(
                "feature_roles",
                {},
            )
        ).items()
    }

    roles["feature_cutoff_utc"] = "metadata"

    metadata_columns = sorted(
        {
            *(
                str(value)
                for value in contract.get(
                    "metadata_columns",
                    [],
                )
            ),
            "feature_cutoff_utc",
        }
    )

    dtype_map = {str(column): str(dataset[column].dtype) for column in dataset.columns}

    null_counts = {str(column): int(dataset[column].isna().sum()) for column in dataset.columns}

    all_columns = [str(column) for column in dataset.columns]

    contract["feature_columns"] = ordered_features
    contract["label_columns"] = ordered_labels
    contract["metadata_columns"] = metadata_columns
    contract["feature_roles"] = roles

    contract["required_columns"] = [
        *ordered_features,
        *ordered_labels,
    ]

    contract["optional_columns"] = sorted(
        set(all_columns) - set(ordered_features) - set(ordered_labels)
    )

    contract["nullable_columns"] = sorted(
        column for column, count in null_counts.items() if count > 0
    )

    contract["non_nullable_columns"] = sorted(
        column for column, count in null_counts.items() if count == 0
    )

    contract["feature_dtypes"] = {column: dtype_map[column] for column in ordered_features}

    schema_payload = {
        "columns": all_columns,
        "dtypes": dtype_map,
        "feature_roles": roles,
        "feature_columns": ordered_features,
        "label_columns": ordered_labels,
    }

    contract["schema_hash"] = stable_hash(schema_payload)

    contract["contract_id"] = "feature_contract_" + contract["schema_hash"][:16]

    contract["wq6_feature_formulas"] = {name: FEATURE_FORMULAS[name] for name in ordered_features}

    contract["wq6_feature_order_source"] = "WQ6_QLIB_DATASET_DESIGN_FREEZE_V1"

    contract["wq6_pit_availability_rule"] = (
        "closed Binance 1m candle usable at candle_open_time + 60 seconds"
    )

    contract["wq6_metadata_role_overrides"] = {
        "feature_cutoff_utc": "metadata",
    }

    contract["contract_hash"] = contract_hash(contract)

    if contract.get("validation_status") != "ok":
        raise QlibDatasetValidationError(
            "shared_feature_contract_validation_blocked:"
            + ",".join(
                str(value)
                for value in contract.get(
                    "validation_errors",
                    [],
                )
            )
        )

    if contract["feature_roles"].get("feature_cutoff_utc") != "metadata":
        raise QlibDatasetValidationError("feature_cutoff_metadata_role_drift")

    if "feature_cutoff_utc" in contract["feature_columns"]:
        raise QlibDatasetValidationError("feature_cutoff_leaked_into_features")

    return contract


def _target_store_hash(
    target_store: Mapping[str, Any],
) -> str:
    payload = {key: value for key, value in target_store.items() if key != "target_store_hash"}
    return stable_hash(payload)


def _build_target_store(
    dataset: pd.DataFrame,
    *,
    feature_contract_hash: str,
    dataset_hash: str,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []

    for row in dataset.itertuples(index=False):
        pnl = float(row.label_economic_net_pnl)
        sign = 1 if pnl > 0.0 else (-1 if pnl < 0.0 else 0)

        records.append(
            {
                "order_id": str(row.order_id),
                "target_expected_value_component": pnl,
                "target_net_pnl": pnl,
                "target_label_sign": sign,
            }
        )

    target_store: dict[str, Any] = {
        "schema_version": "wq6_official_master_financial_target_store_v1",
        "feature_contract_hash": feature_contract_hash,
        "dataset_hash": dataset_hash,
        "primary_target": "target_expected_value_component",
        "auxiliary_targets": [
            "target_net_pnl",
            "target_label_sign",
        ],
        "target_records": records,
        "target_record_count": len(records),
        "economic_label_source": "economic_net_pnl",
        "training_performed": False,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    target_store["target_store_hash"] = _target_store_hash(target_store)
    return target_store


def _true_label_interval_overlap_count(
    frame: pd.DataFrame,
    splits: Sequence[Mapping[str, Any]],
) -> int:
    count = 0

    for split in splits:
        train_indices = list(split.get("_train_indices", []))
        validation_indices = list(split.get("_validation_indices", []))
        test_indices = list(split.get("_test_indices", []))

        eval_indices = validation_indices + test_indices

        if not train_indices or not eval_indices:
            continue

        earliest_eval_open = frame.loc[
            eval_indices,
            "open_time_utc",
        ].min()

        train_close = frame.loc[
            train_indices,
            "close_time_utc",
        ]

        count += int((train_close >= earliest_eval_open).sum())

    return count


def _build_split_manifest(
    dataset: pd.DataFrame,
    *,
    feature_contract: Mapping[str, Any],
    dataset_hash: str,
    target_store_hash: str,
) -> dict[str, Any]:
    """Build deterministic purged walk-forward evidence for WQ6.

    The manifest exposes both the shared leakage-audit counters and the
    stricter WQ6 label-interval overlap counter at top level so consumers
    do not need to infer semantic equivalence between generic and WQ6
    leakage fields.
    """

    splits = build_walkforward_splits(
        dataset,
        embargo_seconds=EMBARGO_SECONDS,
    )

    if not splits:
        raise QlibDatasetValidationError("walkforward_split_generation_failed")

    leakage = audit_leakage(
        dataset,
        splits,
        feature_contract=dict(feature_contract),
        embargo_seconds=EMBARGO_SECONDS,
    )

    true_interval_overlap = _true_label_interval_overlap_count(
        dataset,
        splits,
    )

    if true_interval_overlap != 0:
        raise QlibDatasetValidationError(
            f"true_label_interval_overlap_nonzero:{true_interval_overlap}"
        )

    if leakage.get("leakage_status") != "ok":
        raise QlibDatasetValidationError("shared_leakage_audit_blocked")

    public_splits = [public_split(split) for split in splits]

    manifest: dict[str, Any] = {
        "schema_version": "wq6_official_master_walkforward_split_manifest_v1",
        "feature_contract_hash": feature_contract["contract_hash"],
        "dataset_hash": dataset_hash,
        "target_store_hash": target_store_hash,
        "split_engine_status": "ok",
        "validation_status": "ok",
        "split_count": len(public_splits),
        "split_policy": {
            "type": "deterministic_walkforward",
            "random_split_used": False,
            "shuffle_used": False,
        },
        "purge_applied": True,
        "embargo_applied": True,
        "embargo_seconds": EMBARGO_SECONDS,
        "splits": public_splits,
        "leakage_audit": {
            **leakage,
            "true_label_interval_overlap_count": true_interval_overlap,
        },
        "leakage_status": leakage["leakage_status"],
        "temporal_overlap_count": leakage["temporal_overlap_count"],
        "embargo_violation_count": leakage["embargo_violation_count"],
        "label_interval_overlap_count": true_interval_overlap,
        "true_label_interval_overlap_count": true_interval_overlap,
        "future_columns_in_features_count": leakage["future_columns_in_features_count"],
        "target_columns_in_features_count": leakage["target_columns_in_features_count"],
        "outcome_columns_in_features_count": leakage["outcome_columns_in_features_count"],
        "training_performed": False,
        "safety_flags": dict(SAFETY_FLAGS),
    }

    manifest["split_manifest_hash"] = stable_hash(
        {key: value for key, value in manifest.items() if key != "split_manifest_hash"}
    )

    return manifest


def _build_dataset_manifest(
    dataset: pd.DataFrame,
    *,
    selected_dataset_path: Path,
    source_paths: Sequence[Path],
    feature_contract: Mapping[str, Any],
    design_freeze_sha256: str,
    backfill_validation_sha256: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    dataset_hash = frame_hash(dataset)

    manifest: dict[str, Any] = {
        "schema_version": "wq6_official_master_qlib_dataset_manifest_v1",
        "lineage_type": "post_ocr_official_master_pit_qlib_research_v1",
        "dataset_hash": dataset_hash,
        "selected_training_dataset": str(selected_dataset_path),
        "row_count": int(len(dataset)),
        "column_count": int(len(dataset.columns)),
        "feature_contract_hash": feature_contract["contract_hash"],
        "feature_order": list(feature_contract["feature_columns"]),
        "label_order": list(feature_contract["label_columns"]),
        "source_paths": [str(path) for path in source_paths],
        "source_hashes": {str(path): _file_sha256(path) for path in source_paths},
        "official_master_sha256": OFFICIAL_MASTER_SHA256,
        "design_freeze_sha256": design_freeze_sha256,
        "backfill_validation_sha256": backfill_validation_sha256,
        "min_timestamp_utc": dataset["open_time_utc"].min().isoformat(),
        "max_timestamp_utc": dataset["open_time_utc"].max().isoformat(),
        "symbol_count": int(dataset["symbol"].nunique()),
        "symbols": sorted(dataset["symbol"].unique().tolist()),
        "side_count": int(dataset["side"].nunique()),
        "sides": sorted(dataset["side"].unique().tolist()),
        "pit_eligible_rows": int(evidence["pit_eligible_rows"]),
        "legacy_excluded_rows": int(evidence["legacy_excluded_rows"]),
        "pit_eligible_excluded_rows": int(evidence["pit_eligible_excluded_rows"]),
        "null_counts": {
            str(column): int(dataset[column].isna().sum()) for column in dataset.columns
        },
        "dtype_map": {str(column): str(dataset[column].dtype) for column in dataset.columns},
        "read_only_sources": True,
        "training_performed": False,
        "safety_flags": dict(SAFETY_FLAGS),
        "validation_status": "ok",
        "validation_errors": [],
    }

    return manifest


def build_official_trades_master_qlib_dataset(
    *,
    master_path: str | Path,
    existing_btc_path: str | Path,
    existing_eth_path: str | Path,
    backfill_btc_path: str | Path,
    backfill_eth_path: str | Path,
    design_freeze_path: str | Path,
    backfill_validation_path: str | Path,
    selected_dataset_path: str | Path,
) -> QlibDatasetArtifacts:
    master_path = Path(master_path)
    existing_btc_path = Path(existing_btc_path)
    existing_eth_path = Path(existing_eth_path)
    backfill_btc_path = Path(backfill_btc_path)
    backfill_eth_path = Path(backfill_eth_path)
    design_freeze_path = Path(design_freeze_path)
    backfill_validation_path = Path(backfill_validation_path)
    selected_dataset_path = Path(selected_dataset_path)

    design_freeze = _load_json_contract(
        design_freeze_path,
        expected_sha256=EXPECTED_DESIGN_FREEZE_SHA256,
        label="design_freeze",
    )

    backfill_validation = _load_json_contract(
        backfill_validation_path,
        expected_sha256=EXPECTED_BACKFILL_VALIDATION_SHA256,
        label="backfill_validation",
    )

    if design_freeze.get("schema_version") != ("wq6_qlib_dataset_design_freeze_v1"):
        raise QlibDatasetValidationError("design_freeze_schema_mismatch")

    if backfill_validation.get("status") != "ok":
        raise QlibDatasetValidationError("backfill_validation_not_ok")

    if backfill_validation.get("decision") != ("BACKFILL_VALIDATED_FOR_WQ6_PIT_FEATURE_DATASET"):
        raise QlibDatasetValidationError("backfill_validation_decision_mismatch")

    actual_backfill_hashes = {
        "BTCUSDT": _file_sha256(backfill_btc_path),
        "ETHUSDT": _file_sha256(backfill_eth_path),
    }

    if actual_backfill_hashes != EXPECTED_BACKFILL_SHA256:
        raise QlibDatasetValidationError("backfill_source_sha256_mismatch")

    master = load_official_trades_master(master_path)

    if master.audit.master_sha256 != OFFICIAL_MASTER_SHA256:
        raise QlibDatasetValidationError("official_master_sha256_mismatch")

    market_by_symbol = {
        "BTCUSDT": _combine_market_sources(
            symbol="BTCUSDT",
            existing_path=existing_btc_path,
            backfill_path=backfill_btc_path,
        ),
        "ETHUSDT": _combine_market_sources(
            symbol="ETHUSDT",
            existing_path=existing_eth_path,
            backfill_path=backfill_eth_path,
        ),
    }

    dataset, evidence = build_pit_feature_dataset(
        master.frame,
        market_by_symbol=market_by_symbol,
        expected_eligible_rows=EXPECTED_PIT_ELIGIBLE_ROWS,
    )

    validation_errors: list[str] = []

    if evidence["legacy_excluded_rows"] != EXPECTED_LEGACY_ROWS:
        validation_errors.append("legacy_excluded_row_count_mismatch")

    if evidence["pit_eligible_excluded_rows"] != 0:
        validation_errors.append("pit_eligible_feature_rows_excluded")

    if len(dataset) != EXPECTED_PIT_ELIGIBLE_ROWS:
        validation_errors.append("dataset_row_count_not_equal_pit_eligible_rows")

    feature_columns = _ordered_names(
        design_freeze,
        "feature_contract",
        "ordered_features",
    )
    label_columns = _ordered_names(
        design_freeze,
        "label_contract",
        "ordered_labels",
    )

    if len(feature_columns) != 21:
        validation_errors.append("frozen_feature_count_mismatch")

    if len(label_columns) != 2:
        validation_errors.append("frozen_label_count_mismatch")

    for column in [*feature_columns, *label_columns]:
        if column not in dataset.columns:
            validation_errors.append(f"dataset_missing_required_column:{column}")

    if not dataset.empty:
        required_numeric = dataset[[*feature_columns, *label_columns]].apply(
            pd.to_numeric, errors="coerce"
        )

        if required_numeric.isna().any().any():
            validation_errors.append("required_feature_or_label_nan")

        if not np.isfinite(required_numeric.to_numpy(dtype=float)).all():
            validation_errors.append("required_feature_or_label_non_finite")

        if (dataset["feature_cutoff_utc"] > dataset["open_time_utc"]).any():
            validation_errors.append("feature_cutoff_after_trade_open")

        if dataset["order_id"].duplicated().any():
            validation_errors.append("duplicate_order_id")

    if validation_errors:
        raise QlibDatasetValidationError(
            "dataset_validation_blocked:"
            + ",".join(sorted(set(validation_errors)))
            + ":exclusions="
            + json.dumps(
                evidence["exclusion_counts"],
                sort_keys=True,
            )
        )

    source_paths = [
        master_path,
        existing_btc_path,
        existing_eth_path,
        backfill_btc_path,
        backfill_eth_path,
        design_freeze_path,
        backfill_validation_path,
    ]

    feature_contract = _build_feature_contract(
        dataset,
        design_freeze=design_freeze,
        source_datasets=[str(path) for path in source_paths],
    )

    dataset_manifest = _build_dataset_manifest(
        dataset,
        selected_dataset_path=selected_dataset_path,
        source_paths=source_paths,
        feature_contract=feature_contract,
        design_freeze_sha256=EXPECTED_DESIGN_FREEZE_SHA256,
        backfill_validation_sha256=EXPECTED_BACKFILL_VALIDATION_SHA256,
        evidence=evidence,
    )

    target_store = _build_target_store(
        dataset,
        feature_contract_hash=feature_contract["contract_hash"],
        dataset_hash=dataset_manifest["dataset_hash"],
    )

    split_manifest = _build_split_manifest(
        dataset,
        feature_contract=feature_contract,
        dataset_hash=dataset_manifest["dataset_hash"],
        target_store_hash=target_store["target_store_hash"],
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "decision": "QLIB_DATASET_READY",
        "official_master_sha256": master.audit.master_sha256,
        "dataset_hash": dataset_manifest["dataset_hash"],
        "feature_contract_hash": feature_contract["contract_hash"],
        "target_store_hash": target_store["target_store_hash"],
        "split_manifest_hash": split_manifest["split_manifest_hash"],
        "row_count": int(len(dataset)),
        "feature_count": len(feature_contract["feature_columns"]),
        "label_count": len(feature_contract["label_columns"]),
        "pit_eligible_rows": evidence["pit_eligible_rows"],
        "legacy_excluded_rows": evidence["legacy_excluded_rows"],
        "pit_eligible_excluded_rows": evidence["pit_eligible_excluded_rows"],
        "exclusion_counts": evidence["exclusion_counts"],
        "split_count": split_manifest["split_count"],
        "leakage_status": split_manifest["leakage_status"],
        "true_label_interval_overlap_count": split_manifest["leakage_audit"][
            "true_label_interval_overlap_count"
        ],
        "design_freeze_sha256": EXPECTED_DESIGN_FREEZE_SHA256,
        "backfill_validation_sha256": EXPECTED_BACKFILL_VALIDATION_SHA256,
        "backfill_sha256": dict(EXPECTED_BACKFILL_SHA256),
        "write_performed": False,
        "dataset_materialized": False,
        "training_performed": False,
        "model_promotion_performed": False,
        **SAFETY_FLAGS,
    }

    return QlibDatasetArtifacts(
        dataset=dataset,
        feature_contract=feature_contract,
        dataset_manifest=dataset_manifest,
        target_store=target_store,
        split_manifest=split_manifest,
        report=report,
    )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_safe,
        )
        + "\n"
    ).encode("utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value


def _atomic_write_bytes(
    path: Path,
    content: bytes,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)

    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def persist_qlib_dataset_artifacts(
    artifacts: QlibDatasetArtifacts,
    *,
    output_root: str | Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = Path(output_root)

    paths = {
        "dataset": root / "official_trades_master_qlib_dataset_v1.parquet",
        "feature_contract": root / "official_trades_master_qlib_feature_contract_v1.json",
        "dataset_manifest": root / "official_trades_master_qlib_dataset_manifest_v1.json",
        "target_store": root / "official_trades_master_qlib_target_store_v1.json",
        "split_manifest": root / "official_trades_master_qlib_split_manifest_v1.json",
        "report": root / "official_trades_master_qlib_dataset_report_v1.json",
    }

    if not overwrite:
        existing = [str(path) for path in paths.values() if path.exists()]
        if existing:
            raise QlibDatasetValidationError("output_artifacts_already_exist:" + ",".join(existing))

    root.mkdir(parents=True, exist_ok=True)

    dataset_path = paths["dataset"]
    temporary_dataset = dataset_path.with_suffix(dataset_path.suffix + ".tmp")

    artifacts.dataset.to_parquet(
        temporary_dataset,
        index=False,
    )

    try:
        os.replace(
            temporary_dataset,
            dataset_path,
        )
    finally:
        temporary_dataset.unlink(missing_ok=True)

    manifest = dict(artifacts.dataset_manifest)
    manifest["selected_training_dataset"] = str(dataset_path)
    manifest["materialized_dataset_sha256"] = _file_sha256(dataset_path)

    _atomic_write_bytes(
        paths["feature_contract"],
        _json_bytes(artifacts.feature_contract),
    )
    _atomic_write_bytes(
        paths["dataset_manifest"],
        _json_bytes(manifest),
    )
    _atomic_write_bytes(
        paths["target_store"],
        _json_bytes(artifacts.target_store),
    )
    _atomic_write_bytes(
        paths["split_manifest"],
        _json_bytes(artifacts.split_manifest),
    )

    final_report = dict(artifacts.report)
    final_report["write_performed"] = True
    final_report["dataset_materialized"] = True
    final_report["materialized_paths"] = {key: str(path) for key, path in paths.items()}
    final_report["materialized_dataset_sha256"] = manifest["materialized_dataset_sha256"]

    _atomic_write_bytes(
        paths["report"],
        _json_bytes(final_report),
    )

    return {
        "paths": {key: str(path) for key, path in paths.items()},
        "dataset_sha256": manifest["materialized_dataset_sha256"],
        "feature_contract_sha256": _file_sha256(paths["feature_contract"]),
        "dataset_manifest_sha256": _file_sha256(paths["dataset_manifest"]),
        "target_store_sha256": _file_sha256(paths["target_store"]),
        "split_manifest_sha256": _file_sha256(paths["split_manifest"]),
        "report_sha256": _file_sha256(paths["report"]),
    }


__all__ = [
    "EXPECTED_BACKFILL_SHA256",
    "EXPECTED_BACKFILL_VALIDATION_SHA256",
    "EXPECTED_DESIGN_FREEZE_SHA256",
    "FEATURE_FORMULAS",
    "MARKET_SOURCE_ID",
    "QlibDatasetArtifacts",
    "QlibDatasetValidationError",
    "SCHEMA_VERSION",
    "build_official_trades_master_qlib_dataset",
    "build_pit_feature_dataset",
    "persist_qlib_dataset_artifacts",
]
