from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from smartcrypto.runtime.integrity_traceability_v2 import atomic_write_json


DEFAULT_INPUT_PATH = Path("data/reports/ai_shadow_model_outcomes.jsonl")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_financial_threshold_evaluation_report.json")
DEFAULT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
MINIMUM_RECOMMENDED_SAMPLES = 30
DECISION_GROUPS = ("AI_ACCEPT", "AI_REJECT", "SHADOW_ENTRY", "SHADOW_SKIP")
REQUIRED_COLUMNS = ("matched",)
PROBABILITY_CANDIDATES = ("probability_or_confidence", "probability", "probability_win", "confidence", "proba", "score", "model_confidence")
RETURN_CANDIDATES = ("target_return", "return_pct", "pnl_fechado", "pnl", "pnl_usdt")
DECISION_CANDIDATES = ("decision", "action_shadow", "ai_decision", "shadow_decision", "action")


class FinancialEvaluationError(ValueError):
    pass


def evaluate_ai_shadow_financial_thresholds(
    *,
    input_path: str | Path = DEFAULT_INPUT_PATH,
    report_path: str | Path | None = DEFAULT_REPORT_PATH,
    thresholds: list[float] | tuple[float, ...] = DEFAULT_THRESHOLDS,
    min_samples: int = MINIMUM_RECOMMENDED_SAMPLES,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_file = Path(input_path)
    report_file = Path(report_path) if report_path is not None else None
    if not input_file.exists():
        report = blocked_report(
            reason="missing_input",
            input_path=input_file,
            report_path=report_file,
            thresholds=thresholds,
            min_samples=min_samples,
        )
        write_report(report, report_file)
        return report
    try:
        frame = read_table(input_file)
    except Exception as exc:
        report = blocked_report(
            reason=f"invalid_input:{exc}",
            input_path=input_file,
            report_path=report_file,
            thresholds=thresholds,
            min_samples=min_samples,
        )
        write_report(report, report_file)
        return report
    return evaluate_ai_shadow_financial_thresholds_frame(
        frame=frame,
        input_path=input_file,
        report_path=report_file,
        thresholds=thresholds,
        min_samples=min_samples,
        strict=strict,
        safety_overrides=safety_overrides,
    )


def evaluate_ai_shadow_financial_thresholds_frame(
    *,
    frame: pd.DataFrame,
    input_path: str | Path | None = None,
    report_path: str | Path | None = None,
    thresholds: list[float] | tuple[float, ...] = DEFAULT_THRESHOLDS,
    min_samples: int = MINIMUM_RECOMMENDED_SAMPLES,
    strict: bool = False,
    safety_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report_file = Path(report_path) if report_path is not None else None
    input_label = str(input_path) if input_path is not None else None
    safe = safety_payload(safety_overrides)
    safety_errors = unsafe_safety_flags(safe)
    if strict and safety_errors:
        report = base_report(
            status="blocked",
            reason="unsafe_safety_flags",
            input_path=input_label,
            report_path=report_file,
            thresholds=thresholds,
            min_samples=min_samples,
            safety=safe,
        )
        report["blocking_errors"] = safety_errors
        write_report(report, report_file)
        return report

    validation = validate_input_frame(frame)
    if validation["errors"]:
        report = base_report(
            status="blocked",
            reason=";".join(validation["errors"]),
            input_path=input_label,
            report_path=report_file,
            thresholds=thresholds,
            min_samples=min_samples,
            safety=safe,
        )
        report.update(validation)
        write_report(report, report_file)
        return report

    normalized = normalize_frame(frame, validation)
    matched = normalized.loc[normalized["matched"].eq(True)].copy()
    if matched.empty:
        report = base_report(
            status="blocked",
            reason="no_matched_outcomes",
            input_path=input_label,
            report_path=report_file,
            thresholds=thresholds,
            min_samples=min_samples,
            safety=safe,
        )
        report.update(
            {
                "total_rows": int(len(frame)),
                "matched_rows": 0,
                "unmatched_rows": int(len(frame)),
                "total_decisions": int(len(frame)),
                "matched_outcomes": 0,
                "unmatched_outcomes": int(len(frame)),
            }
        )
        write_report(report, report_file)
        return report

    threshold_values = sorted({round(float(value), 6) for value in thresholds})
    threshold_results = [
        evaluate_threshold(matched, threshold=value) for value in threshold_values
    ]
    group_results = evaluate_groups(matched, threshold_results, threshold_values)
    best_threshold = select_best_threshold(threshold_results)
    sample_warning = int(len(matched)) < int(min_samples)
    report_status = "insufficient_data" if sample_warning else "ok"
    recommended_threshold = best_threshold["threshold"] if best_threshold else None
    recommendation_reason = (
        "low_sample_best_expectancy"
        if sample_warning and recommended_threshold is not None
        else "best_expectancy_then_profit_factor"
        if recommended_threshold is not None
        else "no_threshold_candidate"
    )

    report = base_report(
        status=report_status,
        reason="sample_warning" if sample_warning else "ok",
        input_path=input_label,
        report_path=report_file,
        thresholds=threshold_values,
        min_samples=min_samples,
        safety=safe,
    )
    report.update(
        {
            "total_rows": int(len(frame)),
            "matched_rows": int(len(matched)),
            "unmatched_rows": int(len(frame) - len(matched)),
            "total_decisions": int(len(frame)),
            "matched_outcomes": int(len(matched)),
            "unmatched_outcomes": int(len(frame) - len(matched)),
            "probability_column": validation["probability_column"],
            "return_column": validation["return_column"],
            "decision_column": validation["decision_column"],
            "global_metrics": compute_financial_metrics(matched["return_value"]),
            "threshold_results": threshold_results,
            "group_results": group_results,
            "best_threshold": best_threshold,
            "recommended_threshold": recommended_threshold,
            "recommendation_reason": recommendation_reason,
            "recommendation_confidence": "low" if sample_warning else "medium",
            "threshold_policy": "recommend_only_no_auto_promotion",
            "sample_warning": bool(sample_warning),
            "minimum_recommended_samples": int(min_samples),
            "promotion_allowed": False,
            "auto_promote": False,
            "registry_updated": False,
            "model_promoted": False,
            "signal_producer_updated": False,
            "risk_changed": False,
        }
    )
    write_report(report, report_file)
    return report


def validate_input_frame(frame: pd.DataFrame) -> dict[str, Any]:
    if not isinstance(frame, pd.DataFrame):
        return {"errors": ["input_must_be_dataframe"]}
    columns = [str(column) for column in frame.columns]
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    probability_column = first_numeric_existing(frame, PROBABILITY_CANDIDATES)
    return_column = first_numeric_existing(frame, RETURN_CANDIDATES)
    decision_column = first_nonempty_existing(frame, DECISION_CANDIDATES)
    if probability_column is None:
        missing.append("probability_or_confidence")
    if return_column is None:
        missing.append("target_return_or_pnl")
    if decision_column is None:
        missing.append("decision")
    errors = [f"missing_required_columns:{missing}"] if missing else []
    return {
        "errors": errors,
        "missing_required_columns": missing,
        "probability_column": probability_column,
        "return_column": return_column,
        "decision_column": decision_column,
    }


def normalize_frame(frame: pd.DataFrame, validation: dict[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    result["matched"] = result["matched"].map(normalize_bool)
    result["probability_value"] = pd.to_numeric(
        result[validation["probability_column"]],
        errors="coerce",
    )
    result["return_value"] = pd.to_numeric(result[validation["return_column"]], errors="coerce")
    result["decision_value"] = result[validation["decision_column"]].map(normalize_decision)
    result = result.loc[result["probability_value"].notna() & result["return_value"].notna()].copy()
    return result


def evaluate_groups(
    matched: pd.DataFrame,
    threshold_results: list[dict[str, Any]],
    thresholds: list[float],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for decision in DECISION_GROUPS:
        groups[decision] = build_group_result(
            matched.loc[matched["decision_value"].eq(decision)],
            group=decision,
        )
    if thresholds:
        selected = select_best_threshold(threshold_results)
        threshold = float(selected["threshold"]) if selected else float(thresholds[0])
        groups["threshold_pass"] = build_group_result(
            matched.loc[matched["probability_value"] >= threshold],
            group="threshold_pass",
            threshold=threshold,
        )
        groups["threshold_fail"] = build_group_result(
            matched.loc[matched["probability_value"] < threshold],
            group="threshold_fail",
            threshold=threshold,
        )
    return groups


def evaluate_threshold(frame: pd.DataFrame, *, threshold: float) -> dict[str, Any]:
    passed = frame.loc[frame["probability_value"] >= threshold]
    failed = frame.loc[frame["probability_value"] < threshold]
    metrics = compute_financial_metrics(passed["return_value"])
    return {
        "threshold": float(threshold),
        "threshold_pass_count": int(len(passed)),
        "threshold_fail_count": int(len(failed)),
        "threshold_pass": metrics,
        "threshold_fail": compute_financial_metrics(failed["return_value"]),
        "expectancy": metrics["expectancy"],
        "profit_factor": metrics["profit_factor"],
        "win_rate": metrics["win_rate"],
        "net_pnl": metrics["net_pnl"],
    }


def build_group_result(
    frame: pd.DataFrame,
    *,
    group: str,
    threshold: float | None = None,
) -> dict[str, Any]:
    payload = {
        "group": group,
        "rows": int(len(frame)),
        **compute_financial_metrics(frame["return_value"] if "return_value" in frame.columns else []),
    }
    if threshold is not None:
        payload["threshold"] = float(threshold)
    return payload


def compute_financial_metrics(values: pd.Series | list[float]) -> dict[str, Any]:
    series = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    rows = int(len(series))
    if rows == 0:
        return empty_metrics()
    wins = series.loc[series > 0]
    losses = series.loc[series < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    profit_factor: float | None
    if gross_loss == 0:
        profit_factor = None if gross_profit > 0 else 0.0
    else:
        profit_factor = gross_profit / gross_loss
    win_rate = float(len(wins) / rows)
    loss_rate = float(len(losses) / rows)
    average_win = float(wins.mean()) if len(wins) else 0.0
    average_loss = float(losses.mean()) if len(losses) else 0.0
    average_return = float(series.mean())
    return {
        "rows": rows,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "average_win": average_win,
        "average_loss": average_loss,
        "average_return": average_return,
        "median_return": float(series.median()),
        "expectancy": average_return,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": float(series.sum()),
        "profit_factor": profit_factor,
        "profit_factor_note": "gross_loss_zero" if gross_loss == 0 else "ok",
        "max_drawdown_approx": approximate_max_drawdown(series),
    }


def empty_metrics() -> dict[str, Any]:
    return {
        "rows": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "loss_rate": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "average_return": 0.0,
        "median_return": 0.0,
        "expectancy": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "net_pnl": 0.0,
        "profit_factor": 0.0,
        "profit_factor_note": "no_rows",
        "max_drawdown_approx": 0.0,
    }


def approximate_max_drawdown(values: pd.Series) -> float:
    equity = values.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    if drawdown.empty:
        return 0.0
    return float(abs(drawdown.min()))


def select_best_threshold(threshold_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not threshold_results:
        return None

    def sort_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
        profit_factor = item.get("profit_factor")
        pf_value = float(profit_factor) if profit_factor is not None else float("inf")
        return (
            float(item.get("expectancy", 0.0)),
            pf_value,
            float(item.get("win_rate", 0.0)),
            float(item.get("threshold", 0.0)),
        )

    best = max(threshold_results, key=sort_key)
    return {
        "threshold": best["threshold"],
        "expectancy": best["expectancy"],
        "profit_factor": best["profit_factor"],
        "win_rate": best["win_rate"],
        "net_pnl": best["net_pnl"],
    }


def read_table(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(file_path)
    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix == ".jsonl":
        rows = []
        with file_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return pd.DataFrame(rows)
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return pd.DataFrame(payload["rows"])
        return pd.DataFrame([payload])
    raise FinancialEvaluationError(f"unsupported_input_format:{suffix}")


def parse_thresholds(value: str | None) -> list[float]:
    if not value:
        return list(DEFAULT_THRESHOLDS)
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def first_existing(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in columns), None)


def first_numeric_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate not in frame.columns:
            continue
        values = pd.to_numeric(frame[candidate], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if values.notna().any():
            return candidate
    return first_existing([str(column) for column in frame.columns], candidates)


def first_nonempty_existing(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate not in frame.columns:
            continue
        values = frame[candidate].dropna().astype(str).str.strip()
        if values.ne("").any():
            return candidate
    return first_existing([str(column) for column in frame.columns], candidates)


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "matched", "ok"}


def normalize_decision(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().upper()
    aliases = {
        "ACCEPT": "AI_ACCEPT",
        "REJECT": "AI_REJECT",
        "ENTRY": "SHADOW_ENTRY",
        "SKIP": "SHADOW_SKIP",
    }
    return aliases.get(text, text)


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


def base_report(
    *,
    status: str,
    reason: str,
    input_path: str | Path | None,
    report_path: str | Path | None,
    thresholds: list[float] | tuple[float, ...],
    min_samples: int,
    safety: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "evaluated_at_utc": utc_timestamp(),
        "input_path": str(input_path) if input_path is not None else None,
        "report_path": str(report_path) if report_path is not None else None,
        "total_rows": 0,
        "matched_rows": 0,
        "unmatched_rows": 0,
        "total_decisions": 0,
        "matched_outcomes": 0,
        "unmatched_outcomes": 0,
        "thresholds": [float(value) for value in thresholds],
        "threshold_results": [],
        "group_results": {},
        "best_threshold": None,
        "recommended_threshold": None,
        "recommendation_reason": reason,
        "recommendation_confidence": "low",
        "threshold_policy": "recommend_only_no_auto_promotion",
        "sample_warning": True,
        "minimum_recommended_samples": int(min_samples),
        "promotion_allowed": False,
        "auto_promote": False,
        "registry_updated": False,
        "model_promoted": False,
        "signal_producer_updated": False,
        "risk_changed": False,
        **safety,
    }


def blocked_report(
    *,
    reason: str,
    input_path: str | Path | None,
    report_path: str | Path | None,
    thresholds: list[float] | tuple[float, ...],
    min_samples: int,
) -> dict[str, Any]:
    return base_report(
        status="blocked",
        reason=reason,
        input_path=input_path,
        report_path=report_path,
        thresholds=thresholds,
        min_samples=min_samples,
        safety=safety_payload(),
    )


def write_report(report: dict[str, Any], report_path: Path | None) -> None:
    if report_path is None:
        return
    atomic_write_json(report_path, report, sort_keys=False)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
