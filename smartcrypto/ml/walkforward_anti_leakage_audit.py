from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_DATASET_PATH = Path("data/features/training_dataset.parquet")
DEFAULT_REPORT_PATH = Path("data/reports/phase23_anti_leakage_report.json")
DEFAULT_BASELINE_REQUIREMENTS = ("random", "always_long", "always_short", "no_trade")
PERFECT_METRIC_NAMES = ("accuracy", "precision", "recall", "f1", "roc_auc", "profit_factor")
METADATA_COLUMNS = {
    "trade_id",
    "order_id",
    "decision_id",
    "correlation_id",
    "symbol",
    "pair",
    "side",
    "moeda",
    "timeframe",
    "tf",
    "split",
    "fold",
    "is_train",
    "is_test",
}
TIMESTAMP_HINTS = ("timestamp", "time", "_ts", "_at", "date", "opened", "closed")
POST_DECISION_EXACT = {
    "pnl",
    "pnl_pct",
    "pnl_fechado",
    "realized_pnl",
    "closed_pnl",
    "profit",
    "profit_abs",
    "return_pct",
    "target_return",
    "outcome",
    "outcome_status",
    "mfe_pct",
    "mae_pct",
}


class WalkforwardAntiLeakageAuditError(ValueError):
    pass


def audit_walkforward_anti_leakage(
    *,
    frame: pd.DataFrame | None = None,
    dataset_path: str | Path | None = DEFAULT_DATASET_PATH,
    walkforward_report_path: str | Path | None = None,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    timestamp_column: str = "open_ts",
    target_column: str = "target_win",
    decision_time_column: str | None = None,
    feature_prefix: str = "",
    min_train_rows: int = 10,
    min_test_rows: int = 5,
    require_embargo: bool = False,
    embargo_minutes: int = 60,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_file = Path(report_path) if report_path is not None else None
    dataset_label = str(dataset_path) if dataset_path is not None else None
    safe = safety_payload(safety_overrides)
    if unsafe := unsafe_safety_flags(safe):
        report = empty_report(
            status="blocked",
            reason="unsafe_safety_flags",
            dataset_path=dataset_label,
            report_path=report_file,
            timestamp_column=timestamp_column,
            target_column=target_column,
            decision_time_column=decision_time_column,
            require_embargo=require_embargo,
            embargo_minutes=embargo_minutes,
            strict=strict,
            safety=safe,
        )
        report["blocking_findings"] = [f"unsafe_safety_flag:{item}" for item in unsafe]
        write_json_if_requested(report, report_file)
        return report

    if frame is None:
        if dataset_path is None or not Path(dataset_path).exists():
            report = empty_report(
                status="blocked",
                reason="missing_dataset",
                dataset_path=dataset_label,
                report_path=report_file,
                timestamp_column=timestamp_column,
                target_column=target_column,
                decision_time_column=decision_time_column,
                require_embargo=require_embargo,
                embargo_minutes=embargo_minutes,
                strict=strict,
                safety=safe,
            )
            report["blocking_findings"] = ["missing_dataset"]
            write_json_if_requested(report, report_file)
            return report
        frame = read_table(Path(dataset_path))

    report = build_audit_report(
        frame=frame,
        dataset_path=dataset_label,
        walkforward_report_path=walkforward_report_path,
        report_path=report_file,
        timestamp_column=timestamp_column,
        target_column=target_column,
        decision_time_column=decision_time_column,
        feature_prefix=feature_prefix,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
        require_embargo=require_embargo,
        embargo_minutes=embargo_minutes,
        strict=strict,
        safety=safe,
    )
    write_json_if_requested(report, report_file)
    return report


def build_audit_report(
    *,
    frame: pd.DataFrame,
    dataset_path: str | None,
    walkforward_report_path: str | Path | None,
    report_path: Path | None,
    timestamp_column: str,
    target_column: str,
    decision_time_column: str | None,
    feature_prefix: str,
    min_train_rows: int,
    min_test_rows: int,
    require_embargo: bool,
    embargo_minutes: int,
    strict: bool,
    safety: dict[str, Any],
) -> dict[str, Any]:
    columns = [str(column) for column in frame.columns] if isinstance(frame, pd.DataFrame) else []
    report = empty_report(
        status="ok",
        reason="ok",
        dataset_path=dataset_path,
        report_path=report_path,
        timestamp_column=timestamp_column,
        target_column=target_column,
        decision_time_column=decision_time_column,
        require_embargo=require_embargo,
        embargo_minutes=embargo_minutes,
        strict=strict,
        safety=safety,
    )
    report["rows"] = int(len(frame)) if isinstance(frame, pd.DataFrame) else 0
    report["columns"] = columns

    warnings: list[str] = []
    blocking: list[str] = []
    leakage: list[dict[str, str]] = []

    if not isinstance(frame, pd.DataFrame):
        blocking.append("input_must_be_dataframe")
    elif frame.empty:
        blocking.append("empty_dataset")
    if timestamp_column not in columns:
        blocking.append(f"missing_timestamp_column:{timestamp_column}")
    if target_column not in columns:
        blocking.append(f"missing_target_column:{target_column}")

    feature_columns = resolve_feature_columns(
        columns,
        target_column=target_column,
        timestamp_column=timestamp_column,
        decision_time_column=decision_time_column,
        feature_prefix=feature_prefix,
    )
    target_columns = sorted(
        column for column in columns if is_target_like(column) or column == target_column
    )
    prohibited = []
    for column in feature_columns:
        reason = prohibited_feature_reason(column, target_column=target_column)
        if reason:
            prohibited.append(column)
            leakage.append({"column": column, "reason": reason})
            blocking.append(f"prohibited_feature:{column}:{reason}")

    if decision_time_column and decision_time_column in columns:
        timestamp_findings = audit_feature_timestamps(
            frame,
            decision_time_column=decision_time_column,
            timestamp_column=timestamp_column,
        )
        for finding in timestamp_findings:
            blocking.append(finding["reason"])
            leakage.append(finding)

    split = audit_temporal_split(
        frame,
        timestamp_column=timestamp_column,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
        require_embargo=require_embargo,
        embargo_minutes=embargo_minutes,
    )
    blocking.extend(split.pop("blocking_findings"))

    report_payload = audit_walkforward_report(
        walkforward_report_path,
        strict=strict,
    )
    warnings.extend(report_payload["warnings"])
    blocking.extend(report_payload["blocking_findings"])

    report.update(
        {
            "feature_columns": feature_columns,
            "target_columns": target_columns,
            "prohibited_feature_columns": sorted(set(prohibited)),
            "lookahead_columns": sorted(
                column for column in prohibited if column.lower().startswith("future_ret_")
            ),
            "leakage_findings": leakage,
            **split,
            **report_payload["report_fields"],
        }
    )

    report["warnings"] = sorted(set(warnings))
    report["blocking_findings"] = sorted(set(blocking))
    if report["blocking_findings"]:
        report["status"] = "blocked"
        report["reason"] = ";".join(report["blocking_findings"])
    elif report["warnings"]:
        report["status"] = "warning"
        report["reason"] = ";".join(report["warnings"])
    return report


def resolve_feature_columns(
    columns: list[str],
    *,
    target_column: str,
    timestamp_column: str,
    decision_time_column: str | None,
    feature_prefix: str,
) -> list[str]:
    excluded = {target_column, timestamp_column}
    if decision_time_column:
        excluded.add(decision_time_column)
    excluded.update(METADATA_COLUMNS)
    if feature_prefix:
        features = [column for column in columns if column.startswith(feature_prefix)]
        # Add prohibited-looking columns even when the prefix would hide them.
        features.extend(
            column
            for column in columns
            if column not in features
            and column not in excluded
            and prohibited_feature_reason(column, target_column=target_column)
        )
        return features
    return [
        column
        for column in columns
        if column not in excluded and not is_timestamp_like(column)
    ]


def prohibited_feature_reason(column: str, *, target_column: str) -> str | None:
    lower = column.lower()
    if column == target_column:
        return "target_column_used_as_feature"
    if lower.startswith("future_ret_"):
        return "future_return_feature"
    if lower.startswith("target_"):
        return "target_feature"
    if lower.startswith("label_"):
        return "label_feature_without_target_declaration"
    if lower in POST_DECISION_EXACT:
        return "post_decision_outcome_feature"
    if "outcome" in lower:
        return "post_decision_outcome_feature"
    if lower.startswith(("future_", "next_", "forward_")):
        return "future_derived_feature"
    if lower.startswith("close_"):
        return "future_close_feature_for_open_decision"
    return None


def audit_feature_timestamps(
    frame: pd.DataFrame,
    *,
    decision_time_column: str,
    timestamp_column: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    decision_times = pd.to_datetime(frame[decision_time_column], utc=True, errors="coerce")
    candidate_columns = [
        column
        for column in frame.columns
        if str(column) != decision_time_column
        and str(column) != timestamp_column
        and ("feature" in str(column).lower() and is_timestamp_like(str(column)))
    ]
    for column in candidate_columns:
        feature_times = pd.to_datetime(frame[column], utc=True, errors="coerce")
        if bool((feature_times > decision_times).fillna(False).any()):
            findings.append(
                {
                    "column": str(column),
                    "reason": "feature_timestamp_after_decision_timestamp",
                }
            )
    return findings


def audit_temporal_split(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    min_train_rows: int,
    min_test_rows: int,
    require_embargo: bool,
    embargo_minutes: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "temporal_split_valid": False,
        "embargo_required": bool(require_embargo),
        "embargo_present": False,
        "train_min_timestamp": None,
        "train_max_timestamp": None,
        "test_min_timestamp": None,
        "test_max_timestamp": None,
        "overlap_detected": False,
        "train_rows": 0,
        "test_rows": 0,
        "blocking_findings": [],
    }
    if not isinstance(frame, pd.DataFrame) or frame.empty or timestamp_column not in frame.columns:
        return payload

    working = frame.copy()
    working["_phase23_time"] = pd.to_datetime(working[timestamp_column], utc=True, errors="coerce")
    if working["_phase23_time"].isna().any():
        payload["blocking_findings"].append("timestamp_unparseable")
        return payload
    train, test = split_train_test(working)
    payload["train_rows"] = int(len(train))
    payload["test_rows"] = int(len(test))
    if len(train) < int(min_train_rows):
        payload["blocking_findings"].append("insufficient_train_rows")
    if len(test) < int(min_test_rows):
        payload["blocking_findings"].append("insufficient_test_rows")
    if train.empty or test.empty:
        return payload

    train_min = train["_phase23_time"].min()
    train_max = train["_phase23_time"].max()
    test_min = test["_phase23_time"].min()
    test_max = test["_phase23_time"].max()
    payload.update(
        {
            "train_min_timestamp": to_iso(train_min),
            "train_max_timestamp": to_iso(train_max),
            "test_min_timestamp": to_iso(test_min),
            "test_max_timestamp": to_iso(test_max),
        }
    )
    overlap = bool(train_max >= test_min) or bool(
        set(train["_phase23_time"].astype("int64")).intersection(
            set(test["_phase23_time"].astype("int64"))
        )
    )
    payload["overlap_detected"] = overlap
    payload["temporal_split_valid"] = not overlap and bool(train_max < test_min)
    if overlap:
        payload["blocking_findings"].append("temporal_train_test_overlap")

    embargo_gap = test_min - train_max
    payload["embargo_present"] = bool(
        embargo_gap >= pd.Timedelta(minutes=int(embargo_minutes))
    )
    if require_embargo and not payload["embargo_present"]:
        payload["blocking_findings"].append("embargo_missing_or_too_small")
    return payload


def split_train_test(working: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "split" in working.columns:
        split_values = working["split"].astype(str).str.lower()
        train = working.loc[split_values.isin({"train", "training"})]
        test = working.loc[split_values.isin({"test", "validation", "valid", "eval"})]
        return train, test
    if "is_train" in working.columns:
        train_mask = working["is_train"].map(normalize_bool)
        return working.loc[train_mask], working.loc[~train_mask]
    ordered = working.sort_values("_phase23_time", kind="stable")
    split_at = max(1, int(len(ordered) * 0.7))
    split_at = min(split_at, len(ordered) - 1)
    return ordered.iloc[:split_at], ordered.iloc[split_at:]


def audit_walkforward_report(
    walkforward_report_path: str | Path | None,
    *,
    strict: bool,
) -> dict[str, Any]:
    warnings: list[str] = []
    blocking: list[str] = []
    missing_baselines: list[str] = []
    suspicious: list[dict[str, Any]] = []
    if walkforward_report_path and Path(walkforward_report_path).exists():
        payload = read_json(Path(walkforward_report_path))
        found_baselines = find_baseline_names(payload)
        missing_baselines = [
            name for name in DEFAULT_BASELINE_REQUIREMENTS if name not in found_baselines
        ]
        if missing_baselines:
            finding = f"missing_baselines:{missing_baselines}"
            if strict:
                blocking.append(finding)
            else:
                warnings.append(finding)
        suspicious = find_perfect_metrics(payload)
        if suspicious and not has_perfect_metric_explanation(payload):
            finding = "suspicious_perfect_metrics_without_explanation"
            if strict:
                blocking.append(finding)
            else:
                warnings.append(finding)
    return {
        "warnings": warnings,
        "blocking_findings": blocking,
        "report_fields": {
            "suspicious_perfect_metrics": suspicious,
            "missing_baselines": missing_baselines,
            "baseline_requirements": list(DEFAULT_BASELINE_REQUIREMENTS),
        },
    }


def find_baseline_names(payload: Any) -> set[str]:
    text = json.dumps(payload, sort_keys=True, default=str).lower()
    return {name for name in DEFAULT_BASELINE_REQUIREMENTS if name in text}


def find_perfect_metrics(payload: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    def walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower()
                if normalized in PERFECT_METRIC_NAMES:
                    try:
                        metric_value = float(item)
                    except (TypeError, ValueError):
                        metric_value = -1.0
                    if metric_value == 1.0 or (
                        normalized == "profit_factor" and metric_value >= 999999
                    ):
                        findings.append(
                            {"metric": normalized, "value": item, "path": f"{path}.{key}"}
                        )
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                walk(item, f"{path}[{idx}]")

    walk(payload)
    return findings


def has_perfect_metric_explanation(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = {
        "perfect_metrics_explanation",
        "methodological_explanation",
        "leakage_explanation",
        "perfect_metric_justification",
    }
    return any(bool(payload.get(key)) for key in keys)


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = read_json(path)
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise WalkforwardAntiLeakageAuditError(f"unsupported_input_format:{suffix}")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_if_requested(payload: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )


def empty_report(
    *,
    status: str,
    reason: str,
    dataset_path: str | None,
    report_path: str | Path | None,
    timestamp_column: str,
    target_column: str,
    decision_time_column: str | None,
    require_embargo: bool,
    embargo_minutes: int,
    strict: bool,
    safety: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "audited_at_utc": utc_timestamp(),
        "input_path": dataset_path,
        "report_path": str(report_path) if report_path else None,
        "rows": 0,
        "columns": [],
        "feature_columns": [],
        "target_columns": [],
        "prohibited_feature_columns": [],
        "lookahead_columns": [],
        "temporal_split_valid": False,
        "embargo_required": bool(require_embargo),
        "embargo_present": False,
        "embargo_minutes": int(embargo_minutes),
        "timestamp_column": timestamp_column,
        "target_column": target_column,
        "decision_time_column": decision_time_column,
        "train_min_timestamp": None,
        "train_max_timestamp": None,
        "test_min_timestamp": None,
        "test_max_timestamp": None,
        "train_rows": 0,
        "test_rows": 0,
        "overlap_detected": False,
        "suspicious_perfect_metrics": [],
        "missing_baselines": [],
        "baseline_requirements": list(DEFAULT_BASELINE_REQUIREMENTS),
        "leakage_findings": [],
        "warnings": [],
        "blocking_findings": [],
        "strict": bool(strict),
        **safety,
    }


def is_target_like(column: str) -> bool:
    lower = column.lower()
    return lower.startswith("target_") or lower.startswith("label_")


def is_timestamp_like(column: str) -> bool:
    lower = column.lower()
    return any(hint in lower for hint in TIMESTAMP_HINTS)


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "train"}


def safety_payload(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }
    if overrides:
        payload.update(overrides)
    return payload


def unsafe_safety_flags(payload: dict[str, Any]) -> list[str]:
    unsafe = []
    if payload.get("paper_only") is not True:
        unsafe.append("paper_only")
    if payload.get("shadow_only") is not True:
        unsafe.append("shadow_only")
    if payload.get("runtime_mode") != "paper":
        unsafe.append("runtime_mode")
    for key in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
    ):
        if payload.get(key) is True:
            unsafe.append(key)
    return unsafe


def to_iso(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
