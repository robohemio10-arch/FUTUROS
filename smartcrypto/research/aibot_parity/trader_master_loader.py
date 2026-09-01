"""Strictly read-only Trader Master loader and quality audit."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.data.trade_file_readonly import read_trade_file
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (
    read_trader_master_readonly,
)

from .contracts import (
    CANONICAL_FIELD_SPECS,
    POST_TRADE_OUTCOMES,
    PRE_TRADE_ATTRIBUTES,
    TRADE_SCHEMA_VERSION,
    FieldClassification,
    TraderMasterLoadResult,
    safety_flags,
)
from .source_registry import (
    SourceRegistryError,
    build_source_record,
    resolve_source_artifact,
    stream_sha256,
)


CANONICAL_COLUMNS = tuple(spec.name for spec in CANONICAL_FIELD_SPECS) + (
    "source_row_identity",
    "source_row_fingerprint",
)
DEFAULT_TRADER_MASTER_SOURCE = Path("data/trades/trades_master.parquet")
NULL_TEXT = frozenset({"", "nan", "nat", "none", "null", "<na>"})
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]+$")
NUMBER_PATTERN = re.compile(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)")


class TraderMasterLoadError(RuntimeError):
    """Controlled read-only loading failure."""


def load_trader_master_readonly(
    *,
    project_root: str | Path,
    trader_master_path: str | Path = DEFAULT_TRADER_MASTER_SOURCE,
    source_investment_id: str,
) -> TraderMasterLoadResult:
    root = Path(project_root).resolve()
    try:
        source = resolve_source_artifact(root, trader_master_path)
    except SourceRegistryError as exc:
        raise TraderMasterLoadError(str(exc)) from exc

    if source.suffix.casefold() == ".parquet":
        bundle = read_trader_master_readonly(
            project_root=root,
            trader_master_path=source,
        )
        if bundle.report.get("status") != "ok":
            raise TraderMasterLoadError(str(bundle.report.get("reason", "trader_master_unreadable")))
        frame = pd.DataFrame(bundle.source_rows)
        source_sha_before = str(bundle.report["trader_master_sha256_before"])
        source_sha_after = str(bundle.report["trader_master_sha256_after"])
        source_size_before = int(bundle.report["trader_master_size_before"])
        source_size_after = int(bundle.report["trader_master_size_after"])
        adapter_report = dict(bundle.report)
    else:
        source_size_before = source.stat().st_size
        source_sha_before = stream_sha256(source)
        try:
            with TemporaryDirectory(prefix="aibot-trader-master-readonly-") as temporary:
                copied = Path(temporary) / source.name
                shutil.copyfile(source, copied)
                frame = read_trade_file(copied)
        except (OSError, ValueError, ImportError) as exc:
            raise TraderMasterLoadError(f"trader_master_unreadable:{type(exc).__name__}") from exc
        source_size_after = source.stat().st_size
        source_sha_after = stream_sha256(source)
        adapter_report = {
            "status": "ok" if source_sha_before == source_sha_after else "blocked",
            "reason": "trader_master_readonly_copy_ok",
            "trader_master_temp_copy_used": True,
            "trader_master_hash_preserved": source_sha_before == source_sha_after,
            "trader_master_sha256_before": source_sha_before,
            "trader_master_sha256_after": source_sha_after,
            "trader_master_size_before": source_size_before,
            "trader_master_size_after": source_size_after,
            "writes_trader_master": False,
            "write_performed": False,
        }

    if source_sha_before != source_sha_after or source_size_before != source_size_after:
        raise TraderMasterLoadError("trader_master_changed_during_read")

    canonical = canonicalize_trader_master_frame(
        frame,
        source_investment_id=source_investment_id,
        source_batch_id=f"pending:{source_sha_before}",
    )
    source_record = build_source_record(
        project_root=root,
        artifact_path=source,
        source_investment_id=source_investment_id,
        source_row_count=len(frame),
        source_sha256=source_sha_before,
        source_size_bytes=source_size_before,
    )
    canonical["source_batch_id"] = source_record.source_batch_id
    audit = build_quality_audit(
        raw_frame=frame,
        canonical_frame=canonical,
        source_record=source_record.to_dict(),
        adapter_report=adapter_report,
    )
    return TraderMasterLoadResult(
        source=source_record,
        frame=canonical,
        audit=audit,
        adapter_report=adapter_report,
    )


def canonicalize_trader_master_frame(
    frame: pd.DataFrame,
    *,
    source_investment_id: str,
    source_batch_id: str,
) -> pd.DataFrame:
    source = frame.copy(deep=True)
    canonical = pd.DataFrame(index=source.index)
    canonical["source_investment_id"] = source_investment_id
    canonical["source_batch_id"] = source_batch_id
    canonical["exchange"] = _text_series(_first_series(source, ("exchange", "exchange_source")))
    canonical["bot_instance_id"] = _text_series(_first_series(source, ("bot_instance_id",)))
    canonical["strategy_family"] = _text_series(_first_series(source, ("strategy_family", "strategy")))
    canonical["order_id"] = _text_series(_first_series(source, ("order_id",)))
    canonical["trade_id"] = _text_series(_first_series(source, ("trade_id", "source_trade_id")))
    canonical["symbol"] = _normalize_symbol_series(_first_series(source, ("symbol", "moeda", "pair")))
    canonical["side"] = _normalize_side_series(_first_series(source, ("side", "fechar_side")))
    canonical["leverage"] = _numeric_series(_first_series(source, ("leverage", "alavancagem")))
    canonical["open_time_utc"] = _datetime_series(_first_series(source, ("open_time", "horario_abertura")))
    canonical["close_time_utc"] = _datetime_series(_first_series(source, ("close_time", "horario_fechamento")))
    canonical["open_rate"] = _numeric_series(_first_series(source, ("open_rate", "preco_abertura")))
    canonical["close_rate"] = _numeric_series(_first_series(source, ("close_rate", "preco_fechamento")))
    canonical["stake"] = _numeric_series(_first_series(source, ("stake", "stake_amount")))
    canonical["notional"] = _numeric_series(_first_series(source, ("notional", "raw_notional")))
    canonical["pnl_gross"] = _numeric_series(_first_series(source, ("pnl_gross", "gross_pnl")))
    canonical["fees"] = _numeric_series(_first_series(source, ("fees", "trading_fee")))
    canonical["funding"] = _numeric_series(_first_series(source, ("funding", "funding_fee", "funding_fees")))
    canonical["pnl_net"] = _numeric_series(_first_series(source, ("pnl_net", "net_pnl", "pnl_fechado")))
    canonical["exit_reason"] = _text_series(_first_series(source, ("exit_reason", "close_reason")))
    canonical["duration_seconds"] = (
        canonical["close_time_utc"] - canonical["open_time_utc"]
    ).dt.total_seconds()
    canonical["source_row_number"] = np.arange(1, len(source) + 1, dtype=np.int64)
    canonical["source_row_fingerprint"] = _row_fingerprints(canonical)
    canonical["source_row_identity"] = canonical["source_row_fingerprint"].map(
        lambda value: f"aibot_row_sha256_{value}"
    )
    return canonical.loc[:, CANONICAL_COLUMNS]


def build_quality_audit(
    *,
    raw_frame: pd.DataFrame,
    canonical_frame: pd.DataFrame,
    source_record: Mapping[str, Any],
    adapter_report: Mapping[str, Any],
) -> dict[str, Any]:
    row_count = int(len(raw_frame))
    duplicate_order_rows, duplicate_order_values = _duplicate_counts(canonical_frame["order_id"])
    duplicate_trade_rows, duplicate_trade_values = _duplicate_counts(canonical_frame["trade_id"])
    duplicate_fingerprint_rows, duplicate_fingerprint_values = _duplicate_counts(
        canonical_frame["source_row_fingerprint"]
    )
    raw_open = _first_series(raw_frame, ("open_time", "horario_abertura"))
    raw_close = _first_series(raw_frame, ("close_time", "horario_fechamento"))
    raw_pnl = _first_series(raw_frame, ("pnl_net", "net_pnl", "pnl_fechado"))
    missing_open = _missing_mask(raw_open)
    missing_close = _missing_mask(raw_close)
    missing_pnl = _missing_mask(raw_pnl)
    close_before_open = (
        canonical_frame["close_time_utc"].notna()
        & canonical_frame["open_time_utc"].notna()
        & (canonical_frame["close_time_utc"] < canonical_frame["open_time_utc"])
    )
    non_positive_duration = canonical_frame["duration_seconds"].notna() & canonical_frame[
        "duration_seconds"
    ].le(0)
    valid_close_in_source_order = canonical_frame["close_time_utc"].dropna()
    chronology_inversions = int(valid_close_in_source_order.diff().lt(pd.Timedelta(0)).sum())
    impossible_side = ~canonical_frame["side"].isin(["long", "short"])
    symbol_is_valid = canonical_frame["symbol"].fillna("").map(
        lambda value: bool(SYMBOL_PATTERN.fullmatch(str(value)))
    ).astype(bool)
    inconsistent_symbol = canonical_frame["symbol"].isna() | ~symbol_is_valid
    outliers = _pnl_outlier_report(canonical_frame)
    field_contract = build_field_contract(raw_frame, canonical_frame)
    quality_counts = {
        "duplicate_order_id_row_count": duplicate_order_rows,
        "duplicate_order_id_value_count": duplicate_order_values,
        "duplicate_trade_id_row_count": duplicate_trade_rows,
        "duplicate_trade_id_value_count": duplicate_trade_values,
        "duplicate_fingerprint_row_count": duplicate_fingerprint_rows,
        "duplicate_fingerprint_value_count": duplicate_fingerprint_values,
        "missing_symbol_count": int(canonical_frame["symbol"].isna().sum()),
        "missing_side_count": int(canonical_frame["side"].isna().sum()),
        "missing_open_time_count": int(missing_open.sum()),
        "missing_close_time_count": int(missing_close.sum()),
        "missing_pnl_count": int(missing_pnl.sum()),
        "malformed_open_time_count": int((~missing_open & canonical_frame["open_time_utc"].isna()).sum()),
        "malformed_close_time_count": int((~missing_close & canonical_frame["close_time_utc"].isna()).sum()),
        "malformed_pnl_count": int((~missing_pnl & canonical_frame["pnl_net"].isna()).sum()),
        "close_before_open_count": int(close_before_open.sum()),
        "zero_or_negative_duration_count": int(non_positive_duration.sum()),
        "chronology_inversion_count": chronology_inversions,
        "impossible_side_count": int(impossible_side.sum()),
        "inconsistent_symbol_count": int(inconsistent_symbol.sum()),
        "outlier_count": outliers["outlier_count"],
    }
    has_findings = any(int(value) > 0 for value in quality_counts.values())
    return {
        "status": "blocked" if row_count == 0 else "ok",
        "reason": "empty_trader_master" if row_count == 0 else "quality_audit_completed",
        "quality_status": "warning" if has_findings else "ok",
        "schema_version": TRADE_SCHEMA_VERSION,
        "source": dict(source_record),
        "row_count": row_count,
        "column_count": int(len(raw_frame.columns)),
        "columns": [str(column) for column in raw_frame.columns],
        "quality_counts": quality_counts,
        "outliers": outliers,
        "field_contract": field_contract,
        "pre_trade_attributes": list(PRE_TRADE_ATTRIBUTES),
        "post_trade_outcomes": list(POST_TRADE_OUTCOMES),
        "fee_source_fields_present_but_not_interpreted": [
            name for name in ("taxa_1", "taxa_2") if name in raw_frame.columns
        ],
        "fee_semantics_status": "UNAVAILABLE_UNAPPROVED_SOURCE_SEMANTICS",
        "funding_semantics_status": (
            "AVAILABLE_DIRECT_SOURCE"
            if any(name in raw_frame.columns for name in ("funding", "funding_fee", "funding_fees"))
            else "UNAVAILABLE_IN_SOURCE"
        ),
        "rows_removed": 0,
        "outliers_removed": 0,
        "source_mutated": False,
        "adapter_report": dict(adapter_report),
        "p0_findings": 0,
        "p1_findings": 0,
        "safety_flags": safety_flags(),
    }


def build_field_contract(
    raw_frame: pd.DataFrame,
    canonical_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    columns = {str(column) for column in raw_frame.columns}
    rows: list[dict[str, Any]] = []
    for spec in CANONICAL_FIELD_SPECS:
        source_column = next((alias for alias in spec.aliases if alias in columns), None)
        if spec.classification is FieldClassification.DERIVED:
            observed_classification = FieldClassification.DERIVED
        elif source_column is None and spec.classification is FieldClassification.OPTIONAL:
            observed_classification = FieldClassification.UNAVAILABLE
        else:
            observed_classification = spec.classification
        rows.append(
            {
                "field": spec.name,
                "classification": observed_classification.value,
                "declared_classification": spec.classification.value,
                "source_column": source_column,
                "available": bool(spec.name in canonical_frame and canonical_frame[spec.name].notna().any()),
                "null_count": (
                    int(canonical_frame[spec.name].isna().sum())
                    if spec.name in canonical_frame
                    else len(canonical_frame)
                ),
                "pre_trade_attribute": spec.pre_trade_attribute,
                "post_trade_outcome": spec.post_trade_outcome,
            }
        )
    return rows


def _first_series(frame: pd.DataFrame, aliases: tuple[str, ...]) -> pd.Series:
    selected = next((alias for alias in aliases if alias in frame.columns), None)
    if selected is None:
        return pd.Series(pd.NA, index=frame.index, dtype="object")
    return frame[selected]


def _text_series(series: pd.Series) -> pd.Series:
    def normalize(value: object) -> str | None:
        if value is None or pd.isna(value):
            return None
        text = " ".join(str(value).strip().split())
        return None if text.casefold() in NULL_TEXT else text

    return series.map(normalize, na_action=None).astype("string")


def _normalize_symbol_series(series: pd.Series) -> pd.Series:
    text = _text_series(series)
    return text.map(
        lambda value: re.sub(r"[^A-Z0-9]", "", str(value).upper()) if pd.notna(value) else pd.NA
    ).astype("string")


def _normalize_side_series(series: pd.Series) -> pd.Series:
    mapping = {
        "long": "long",
        "fechar long": "long",
        "close long": "long",
        "short": "short",
        "fechar short": "short",
        "close short": "short",
    }
    text = _text_series(series)
    return text.map(
        lambda value: mapping.get(str(value).casefold()) if pd.notna(value) else pd.NA
    ).astype("string")


def _numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.map(_parse_numeric_value, na_action=None), errors="coerce").astype(float)


def _parse_numeric_value(value: object) -> float | None:
    if value is None or pd.isna(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).strip().replace("\u00a0", " ")
    if text.casefold() in NULL_TEXT:
        return None
    matches = NUMBER_PATTERN.findall(text)
    if len(matches) != 1:
        return None
    token = matches[0]
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        token = token.replace(",", ".")
    try:
        number = float(token)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _datetime_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True, format="mixed")


def _missing_mask(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip().str.casefold()
    return series.isna() | text.isin(NULL_TEXT)


def _row_fingerprints(frame: pd.DataFrame) -> pd.Series:
    fields = (
        "symbol",
        "side",
        "open_time_utc",
        "close_time_utc",
        "open_rate",
        "close_rate",
        "pnl_net",
    )

    def digest(row: pd.Series) -> str:
        payload: dict[str, Any] = {}
        for field in fields:
            value = row[field]
            if pd.isna(value):
                payload[field] = None
            elif isinstance(value, pd.Timestamp):
                payload[field] = value.isoformat().replace("+00:00", "Z")
            elif isinstance(value, (float, np.floating)):
                payload[field] = format(float(value), ".12g")
            else:
                payload[field] = str(value)
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    return frame.loc[:, fields].apply(digest, axis=1).astype("string")


def _duplicate_counts(series: pd.Series) -> tuple[int, int]:
    observed = series.dropna().astype("string").str.strip()
    observed = observed.loc[observed.ne("")]
    duplicated = observed.duplicated(keep=False)
    return int(duplicated.sum()), int(observed.loc[duplicated].nunique())


def _pnl_outlier_report(frame: pd.DataFrame) -> dict[str, Any]:
    pnl = pd.to_numeric(frame["pnl_net"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = pnl.dropna()
    if valid.empty:
        return {"method": "iqr_3x", "outlier_count": 0, "lower_bound": None, "upper_bound": None, "sample": []}
    q1 = float(valid.quantile(0.25))
    q3 = float(valid.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - 3.0 * iqr
    upper = q3 + 3.0 * iqr
    mask = pnl.lt(lower) | pnl.gt(upper)
    sample_frame = frame.loc[mask, ["source_row_number", "symbol", "side", "pnl_net"]].copy()
    sample_frame["abs_pnl"] = sample_frame["pnl_net"].abs()
    sample = sample_frame.sort_values(["abs_pnl", "source_row_number"], ascending=[False, True]).head(20)
    return {
        "method": "iqr_3x",
        "outlier_count": int(mask.sum()),
        "lower_bound": lower,
        "upper_bound": upper,
        "sample": [
            {
                "source_row_number": int(row.source_row_number),
                "symbol": None if pd.isna(row.symbol) else str(row.symbol),
                "side": None if pd.isna(row.side) else str(row.side),
                "pnl_net": float(row.pnl_net),
            }
            for row in sample.itertuples(index=False)
        ],
    }
