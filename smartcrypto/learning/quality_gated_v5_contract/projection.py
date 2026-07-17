"""Research-only orchestration for the V5 quality-gated projection."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .anti_leakage import audit_feature_names, audit_temporal_frame
from .contracts import (
    DECISION_RESEARCH,
    DEFAULT_MARKET_FEATURES,
    DEFAULT_MODEL_PATH,
    DEFAULT_OFFICIAL_QUALITY_GATED,
    DEFAULT_REPORT_JSON,
    DEFAULT_REPORT_MARKDOWN,
    DEFAULT_REPORT_ROWS_JSONL,
    DEFAULT_TRADE_ENRICHED,
    EXPECTED_MODEL_SHA256,
    MODEL_FEATURES,
    SAFETY_FLAGS,
    SCHEMA_VERSION,
    SNAPSHOT_TIMESTAMP_SEMANTICS,
)
from .eligibility import build_eligibility, eligibility_summary, normalize_trade_id
from .feature_quality import (
    audit_feature_quality,
    audit_prior_feature_lineage,
    build_model_feature_frame,
    normalize_side,
    normalize_symbol,
)
from .freshness import evaluate_freshness_frame
from .nonregression import compare_official_projection, identity_set
from .provenance import classify_provenance_frame, provenance_summary
from .reporting import evidence_hash, json_safe, write_reports


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_record(source_id: str, path: Path) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "path": str(path),
        "exists": path.is_file(),
        "sha256": file_sha256(path),
    }


def load_frame(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ValueError(f"unsupported_input_format:{suffix}")


def trade_open_column(frame: pd.DataFrame) -> str | None:
    for candidate in ("open_ts", "open_time_utc", "horario_abertura"):
        if candidate in frame.columns:
            return candidate
    return None


def normalized_trade_metadata(trades: pd.DataFrame) -> pd.DataFrame:
    trade = trades.reset_index(drop=True)
    symbol_source = (
        trade["symbol"] if "symbol" in trade.columns else trade.get("moeda", "")
    )
    side_source = (
        trade["fechar_side"]
        if "fechar_side" in trade.columns
        else trade.get("side", "")
    )
    open_column = trade_open_column(trade)
    open_source = (
        trade[open_column]
        if open_column is not None
        else pd.Series([pd.NaT] * len(trade))
    )
    return pd.DataFrame(
        {
            "trade_id": (
                trade["trade_id"].map(normalize_trade_id)
                if "trade_id" in trade.columns
                else pd.Series([""] * len(trade))
            ),
            "symbol": pd.Series(symbol_source, index=trade.index).map(
                normalize_symbol
            ),
            "side": pd.Series(side_source, index=trade.index).map(normalize_side),
            "open_time_utc": pd.to_datetime(
                open_source, errors="coerce", utc=True
            ),
        }
    )


def freshness_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "timestamp_semantics": SNAPSHOT_TIMESTAMP_SEMANTICS,
        "stale_1m_rows": int(frame["snapshot_1m_is_stale"].sum()),
        "stale_5m_rows": int(frame["snapshot_5m_is_stale"].sum()),
        "future_1m_rows": int(frame["snapshot_1m_is_future"].sum()),
        "future_5m_rows": int(frame["snapshot_5m_is_future"].sum()),
        "future_snapshot_rows": int(
            (
                frame["snapshot_1m_is_future"]
                | frame["snapshot_5m_is_future"]
            ).sum()
        ),
        "in_progress_1m_rows": int(
            frame["snapshot_1m_is_in_progress"].sum()
        ),
        "in_progress_5m_rows": int(
            frame["snapshot_5m_is_in_progress"].sum()
        ),
        "in_progress_snapshot_rows": int(
            (
                frame["snapshot_1m_is_in_progress"]
                | frame["snapshot_5m_is_in_progress"]
            ).sum()
        ),
        "missing_1m_rows": int(frame["snapshot_1m_is_missing"].sum()),
        "missing_5m_rows": int(frame["snapshot_5m_is_missing"].sum()),
        "stale_1m_snapshots_accepted": 0,
        "stale_5m_snapshots_accepted": 0,
        "future_1m_snapshots_accepted": 0,
        "future_5m_snapshots_accepted": 0,
        "in_progress_1m_snapshots_accepted": 0,
        "in_progress_5m_snapshots_accepted": 0,
    }


def grouped_feature_null_rates(
    features: pd.DataFrame,
    metadata: pd.DataFrame,
    provenance: pd.DataFrame,
) -> dict[str, Any]:
    """Calculate deterministic null-rate slices without nullable-groupby failures.

    Pandas can raise ``ValueError: Categorical categories cannot be null`` when
    ``groupby(..., dropna=False).groups`` operates on nullable string data. The
    real 3,562-row universe contains missing opening timestamps, which generate
    missing month labels. Missing grouping keys are therefore normalized to an
    explicit sentinel before grouping. The operation remains read-only and
    preserves the original row indices used to slice the feature matrix.
    """

    numeric = features.apply(pd.to_numeric, errors="coerce")
    joined = metadata.copy()
    joined["provenance_contract"] = provenance[
        "provenance_contract"
    ].astype(str)
    joined["month"] = joined["open_time_utc"].dt.strftime("%Y-%m").astype(
        "string"
    )

    result: dict[str, Any] = {}
    for group_name in ("provenance_contract", "symbol", "side", "month"):
        group_payload: dict[str, Any] = {}
        group_values = joined[group_name].astype("object")
        group_values = group_values.where(
            pd.notna(group_values), "<MISSING>"
        )
        grouped = joined.assign(_group_value=group_values).groupby(
            "_group_value",
            sort=True,
            observed=True,
        )
        for group_value, group_frame in grouped:
            rates = numeric.loc[group_frame.index].isna().mean()
            group_payload[str(group_value)] = {
                str(feature): float(rate)
                for feature, rate in rates.items()
                if float(rate) > 0.0
            }
        result[group_name] = group_payload

    v5_indices = provenance.index[
        provenance["provenance_contract"].eq("ocr_v5_20260714")
    ]
    v5_rates = (
        numeric.loc[list(v5_indices)].isna().mean()
        if len(v5_indices)
        else pd.Series(dtype=float)
    )
    result["tail_v5"] = {
        str(feature): float(rate)
        for feature, rate in v5_rates.items()
        if float(rate) > 0.0
    }
    return result


def build_empty_report(
    *,
    root: Path,
    sources: list[dict[str, Any]],
    blockers: list[str],
    write_report: bool,
    output_paths: dict[str, str],
    generated_at_utc: str,
) -> dict[str, Any]:
    safety = dict(SAFETY_FLAGS)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": blockers[0] if blockers else "required_sources_unavailable",
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at_utc,
        "project_root": str(root),
        "input_sources": sources,
        "universe_rows": 0,
        "row_detail_records": 0,
        "blockers": sorted(set(blockers)),
        "warnings": [],
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
        "row_records": [],
    }
    report["evidence_hash"] = evidence_hash(report)
    return report


def build_quality_gated_v5_contract_report(
    *,
    project_root: str | Path,
    trade_enriched_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    official_quality_gated_path: str | Path | None = None,
    model_path: str | Path | None = None,
    max_age_1m_seconds: int = 120,
    max_age_5m_seconds: int = 600,
    timestamp_semantics: str = SNAPSHOT_TIMESTAMP_SEMANTICS,
    expected_model_sha256: str | None = EXPECTED_MODEL_SHA256,
    expected_v5_rows: int | None = 504,
    write_report: bool = False,
    report_json_path: str | Path | None = None,
    report_rows_jsonl_path: str | Path | None = None,
    report_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    trade_path = resolve(root, trade_enriched_path, DEFAULT_TRADE_ENRICHED)
    market_path = resolve(root, market_features_path, DEFAULT_MARKET_FEATURES)
    official_path = resolve(
        root, official_quality_gated_path, DEFAULT_OFFICIAL_QUALITY_GATED
    )
    resolved_model_path = resolve(root, model_path, DEFAULT_MODEL_PATH)
    report_json = resolve(root, report_json_path, DEFAULT_REPORT_JSON)
    report_jsonl = resolve(
        root, report_rows_jsonl_path, DEFAULT_REPORT_ROWS_JSONL
    )
    report_markdown = resolve(
        root, report_markdown_path, DEFAULT_REPORT_MARKDOWN
    )

    sources = [
        source_record("trade_enriched", trade_path),
        source_record("market_features", market_path),
        source_record("official_quality_gated", official_path),
        source_record("active_model_bytes_only", resolved_model_path),
    ]
    before_hashes = {item["source_id"]: item["sha256"] for item in sources}
    blockers = [
        f"missing_required_source:{item['source_id']}"
        for item in sources
        if not item["exists"]
    ]
    output_paths = {
        "json": str(report_json),
        "rows_jsonl": str(report_jsonl),
        "markdown": str(report_markdown),
    }
    if blockers:
        return build_empty_report(
            root=root,
            sources=sources,
            blockers=blockers,
            write_report=write_report,
            output_paths=output_paths,
            generated_at_utc=generated_at,
        )

    if (
        expected_model_sha256
        and before_hashes["active_model_bytes_only"]
        != expected_model_sha256
    ):
        blockers.append("active_model_sha256_mismatch")

    try:
        trades = load_frame(trade_path).reset_index(drop=True)
        market = load_frame(market_path).reset_index(drop=True)
        official = load_frame(official_path).reset_index(drop=True)
    except Exception as exc:
        blockers.append(f"source_read_failed:{exc.__class__.__name__}")
        return build_empty_report(
            root=root,
            sources=sources,
            blockers=blockers,
            write_report=write_report,
            output_paths=output_paths,
            generated_at_utc=generated_at,
        )

    open_column = trade_open_column(trades)
    if open_column is None:
        blockers.append("trade_enriched_missing_open_time_column")
        trades["open_ts"] = pd.NaT
        open_column = "open_ts"

    provenance = classify_provenance_frame(trades)
    freshness = evaluate_freshness_frame(
        trades,
        open_time_column=open_column,
        max_age_1m_seconds=max_age_1m_seconds,
        max_age_5m_seconds=max_age_5m_seconds,
        timestamp_semantics=timestamp_semantics,
    )

    try:
        model_features, snapshots = build_model_feature_frame(trades, market)
        feature_quality, feature_summary = audit_feature_quality(
            model_features
        )
        lineage = audit_prior_feature_lineage(trades, snapshots)
    except Exception as exc:
        blockers.append(f"feature_projection_failed:{exc.__class__.__name__}")
        model_features = pd.DataFrame(
            np.nan,
            index=range(len(trades)),
            columns=list(MODEL_FEATURES),
        )
        feature_quality, feature_summary = audit_feature_quality(
            model_features
        )
        lineage = pd.DataFrame(index=range(len(trades)))

    feature_name_audit = audit_feature_names(list(MODEL_FEATURES))
    if feature_name_audit["status"] != "ok":
        blockers.extend(feature_name_audit["block_reasons"])
    temporal_leakage = audit_temporal_frame(freshness)
    eligibility = build_eligibility(
        trades,
        provenance,
        freshness,
        feature_quality,
        temporal_leakage,
        feature_name_audit=feature_name_audit,
    )
    metadata = normalized_trade_metadata(trades)
    nonregression = compare_official_projection(official, eligibility)
    provenance_stats = provenance_summary(provenance)
    freshness_stats = freshness_summary(freshness)
    eligibility_stats = eligibility_summary(eligibility)

    if (
        expected_v5_rows is not None
        and provenance_stats["v5_recognized_rows"] != expected_v5_rows
    ):
        blockers.append(
            "v5_provenance_recognized_count_mismatch:"
            f"{provenance_stats['v5_recognized_rows']}!={expected_v5_rows}"
        )
    if provenance_stats["unknown_rows"] > 0:
        blockers.append("unknown_provenance_rows_detected")
    if provenance_stats["ambiguous_rows"] > 0:
        blockers.append("ambiguous_provenance_rows_detected")
    if nonregression["status"] == "blocked":
        blockers.append("nonregression_identity_gate_blocked")
    if feature_summary["schema_missing_features"]:
        blockers.append("model_feature_schema_incomplete")

    official_ids = identity_set(official)
    combined = pd.concat(
        [
            metadata,
            provenance,
            freshness,
            feature_quality,
            lineage,
            temporal_leakage,
            eligibility.drop(columns=["trade_id"]),
        ],
        axis=1,
    )
    combined["official_membership"] = combined["trade_id"].isin(
        official_ids
    )
    combined["projected_membership"] = combined[
        "eligible_for_model_training"
    ].astype(bool)
    row_records = [
        json_safe(record) for record in combined.to_dict(orient="records")
    ]

    after_hashes = {
        item["source_id"]: file_sha256(Path(item["path"]))
        for item in sources
    }
    unchanged = before_hashes == after_hashes
    if not unchanged:
        blockers.append("input_source_hash_changed_during_projection")

    status = "blocked" if blockers else "ok"
    if status == "ok" and nonregression["status"] == "review_required":
        reason = "projection_ready_with_explained_official_reduction"
    elif status == "ok":
        reason = "quality_gated_v5_contract_projection_ready"
    else:
        reason = sorted(set(blockers))[0]

    safety = dict(SAFETY_FLAGS)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": DECISION_RESEARCH,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "input_sources": sources,
        "input_hashes_before": before_hashes,
        "input_hashes_after": after_hashes,
        "input_sources_unchanged": unchanged,
        "universe_rows": int(len(trades)),
        "row_detail_records": int(len(row_records)),
        "model_contract": {
            "expected_sha256": expected_model_sha256,
            "actual_sha256": before_hashes["active_model_bytes_only"],
            "hash_match": (
                expected_model_sha256 is None
                or before_hashes["active_model_bytes_only"]
                == expected_model_sha256
            ),
            "model_features_count": len(MODEL_FEATURES),
            "model_features": list(MODEL_FEATURES),
            "model_deserialization_performed": False,
        },
        "provenance": provenance_stats,
        "freshness": freshness_stats,
        "feature_quality": {
            **feature_summary,
            "null_rate_slices": grouped_feature_null_rates(
                model_features,
                metadata,
                provenance,
            ),
        },
        "anti_leakage": {
            "feature_name_audit": feature_name_audit,
            "temporal_leakage_blocked_rows": int(
                temporal_leakage["temporal_leakage_status"]
                .eq("blocked")
                .sum()
            ),
        },
        "eligibility": eligibility_stats,
        "nonregression": nonregression,
        "blockers": sorted(set(blockers)),
        "warnings": (
            ["official_projection_reduction_requires_formal_review"]
            if nonregression["status"] == "review_required"
            else []
        ),
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_paths": output_paths,
        **safety,
        "safety_flags": safety,
        "row_records": row_records,
    }
    report["evidence_hash"] = evidence_hash(report)

    if write_report:
        write_reports(
            project_root=root,
            report=report,
            row_records=row_records,
            report_json=report_json,
            report_rows_jsonl=report_jsonl,
            report_markdown=report_markdown,
        )
        report["write_performed"] = True
    return report
