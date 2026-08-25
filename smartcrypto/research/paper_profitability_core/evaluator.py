"""Deterministic, point-in-time Paper profitability evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
import yaml

from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    load_observability_config,
)
from smartcrypto.execution.paper_profitability_policy_v1 import (
    PaperCandidateProfileV1,
    cooldown_deadline,
    decide_direction,
    evaluate_candidate_policy,
)
from smartcrypto.learning.walkforward.purged_split_engine import (
    build_walkforward_splits,
)
from smartcrypto.qlib_engine.predictor import _transform_features
from smartcrypto.qlib_engine.sklearn_compatibility import load_sklearn_artifact
from smartcrypto.research.paper_edge_foundation.foundation import (
    SourceIntegrityError,
    prepare_closed_trades,
    read_authoritative_paper_source,
)

SCHEMA_VERSION = "paper_profitability_core_v1"
DEFAULT_DB_PATH = Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
DEFAULT_FEATURES_PATH = Path("data/features/market_features_60d.parquet")
DEFAULT_MODEL_PATH = Path("data/models/qlib_market_model.joblib")
DEFAULT_OUTPUT_PATH = Path("data/reports/paper_profitability_core_v1.json")
ACCOUNTING_TOLERANCE_USDT = 1e-4
STOP_REASONS = frozenset({"stop_loss", "stoploss", "trailing_stop_loss"})

THRESHOLDS = ((0.55, 0.45), (0.60, 0.40), (0.65, 0.35))
SCENARIO_MATRIX = tuple(
    (long_probability, short_probability, regime_gate_enabled, cooldown_minutes)
    for long_probability, short_probability in THRESHOLDS
    for regime_gate_enabled in (False, True)
    for cooldown_minutes in (0, 5, 15, 30)
)

SAFETY_FLAGS = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "live": False,
    "canary": False,
    "real_orders": False,
    "model_promotion": False,
    "registry_write": False,
    "runtime_write": False,
    "risk_limits_financial_change": False,
    "sends_orders": False,
    "exchange_private_access": False,
}

SNAPSHOT_SANITY_PROBABILITIES = {
    "BTCUSDT": 0.6093397332227777,
    "ETHUSDT": 0.5072025956314988,
}


@dataclass(frozen=True)
class FinancialMetrics:
    trade_count: int
    net_pnl: float
    expectancy: float
    profit_factor: float | None
    max_drawdown: float
    win_rate: float


def evaluate_paper_candidate_profile_preflight(
    *,
    project_root: str | Path,
    signal_config_path: str | Path = "config/signal_producer.yml",
    ledger_config_path: str | Path = "config/decision_ledger_paper_observability.yml",
) -> dict[str, Any]:
    """Validate the approved candidate and snapshot sanity probes without writes."""

    root = Path(project_root).resolve()
    signal_path = _resolve(root, signal_config_path, Path(signal_config_path))
    ledger_path = _resolve(root, ledger_config_path, Path(ledger_config_path))
    errors: list[str] = []
    profile = PaperCandidateProfileV1()
    try:
        signal_payload = yaml.safe_load(signal_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        signal_payload = {}
        errors.append("signal_config_missing_or_invalid")
    policy = signal_payload.get("policy", {})
    if not isinstance(policy, Mapping):
        policy = {}
        errors.append("signal_policy_missing_or_invalid")

    expected_policy = {
        "profile_id": profile.profile_id,
        "long_probability": profile.long_probability,
        "short_probability": profile.short_probability,
        "regime_gate_enabled": profile.regime_gate_enabled,
        "cooldown_minutes": profile.cooldown_minutes,
        "top_n_can_authorize_trade": profile.top_n_can_authorize_trade,
        "decision_ledger_enabled": profile.decision_ledger_enabled,
    }
    for field, expected in expected_policy.items():
        if policy.get(field) != expected:
            errors.append(f"candidate_policy_mismatch:{field}")
    if signal_payload.get("runtime_mode") != "paper":
        errors.append("candidate_runtime_mode_not_paper")

    try:
        ledger = load_observability_config(ledger_path)
    except (OSError, TypeError, ValueError):
        ledger = None
        errors.append("decision_ledger_config_missing_or_invalid")
    if ledger is not None and not (ledger.enabled and ledger.writer_enabled):
        errors.append("decision_ledger_not_enabled_for_paper")

    sanity_checks: dict[str, dict[str, Any]] = {}
    expected_sides = {"BTCUSDT": "long", "ETHUSDT": "no_trade"}
    for symbol, probability in SNAPSHOT_SANITY_PROBABILITIES.items():
        decision = decide_direction(
            probability,
            long_probability=profile.long_probability,
            short_probability=profile.short_probability,
        )
        expected_side = expected_sides[symbol]
        passed = decision.proposed_side == expected_side
        if not passed:
            errors.append(f"snapshot_sanity_direction_mismatch:{symbol}")
        sanity_checks[symbol] = {
            "prob_up": probability,
            "expected_side": expected_side,
            "proposed_side": decision.proposed_side,
            "final_decision": (
                "NO_TRADE"
                if decision.proposed_side == "no_trade"
                else "PENDING_REGIME_AND_RISK"
            ),
            "score": decision.score,
            "confidence": decision.confidence,
            "passed": passed,
        }

    ready = not errors
    return {
        "schema_version": "paper_profitability_candidate_profile_preflight_v1",
        "status": "ok" if ready else "blocked",
        "reason": "paper_candidate_profile_ready" if ready else errors[0],
        "paper_candidate_profile": "READY" if ready else "BLOCKED",
        "long_threshold": profile.long_probability,
        "short_threshold": profile.short_probability,
        "regime_gate": profile.regime_gate_enabled,
        "cooldown_minutes": profile.cooldown_minutes,
        "top_n_authorization": profile.top_n_can_authorize_trade,
        "decision_ledger": profile.decision_ledger_enabled,
        "profile": profile.to_dict(),
        "signal_config_path": str(signal_path),
        "ledger_config_path": str(ledger_path),
        "decision_ledger_preflight_required": True,
        "decision_ledger_runtime_preflight_status": "not_executed_rollout_required",
        "decision_ledger_configured": bool(
            ledger is not None and ledger.enabled and ledger.writer_enabled
        ),
        "snapshot_sanity_checks": sanity_checks,
        "validation_errors": errors,
        "write_performed": False,
        "runtime_execution_performed": False,
        "paper_only": True,
        "live": False,
        "canary": False,
        "real_orders": False,
        "exchange_private_access": False,
        "model_promotion": False,
        "changes_leverage": False,
        "changes_stake": False,
        "changes_roi": False,
        "changes_stoploss": False,
    }


def evaluate_paper_profitability_core(
    *,
    project_root: str | Path,
    paper_db_path: str | Path | None = None,
    market_features_path: str | Path | None = None,
    model_path: str | Path | None = None,
    output_path: str | Path | None = None,
    write_report: bool = False,
) -> dict[str, Any]:
    """Evaluate the frozen current model without changing Paper configuration."""

    root = Path(project_root).resolve()
    database = _resolve(root, paper_db_path, DEFAULT_DB_PATH)
    features_file = _resolve(root, market_features_path, DEFAULT_FEATURES_PATH)
    model_file = _resolve(root, model_path, DEFAULT_MODEL_PATH)
    report_file = _resolve(root, output_path, DEFAULT_OUTPUT_PATH)
    validation_errors: list[str] = []
    warnings: list[str] = []

    if write_report and not _is_relative_to(
        report_file, (root / "data" / "reports").resolve()
    ):
        return _blocked_report(
            reason="report_output_path_outside_data_reports",
            validation_errors=["report_output_path_outside_data_reports"],
            database=database,
            features_file=features_file,
            model_file=model_file,
            report_file=report_file,
            write_report=write_report,
        )

    try:
        source = read_authoritative_paper_source(database)
        closed, source_summary = prepare_closed_trades(source["trades"])
    except SourceIntegrityError as exc:
        return _blocked_report(
            reason=exc.reason,
            validation_errors=[exc.reason],
            database=database,
            features_file=features_file,
            model_file=model_file,
            report_file=report_file,
            write_report=write_report,
        )

    if not features_file.exists():
        validation_errors.append("market_features_missing")
    if not model_file.exists():
        validation_errors.append("model_missing")
    if validation_errors:
        return _blocked_report(
            reason=validation_errors[0],
            validation_errors=validation_errors,
            database=database,
            features_file=features_file,
            model_file=model_file,
            report_file=report_file,
            write_report=write_report,
        )

    accounting = build_accounting_evaluation_frame(closed)
    feature_rows = pd.read_parquet(features_file)
    aligned, alignment = align_features_point_in_time(accounting, feature_rows)
    try:
        evaluation, model_evidence = score_with_frozen_model(aligned, model_file)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        return _blocked_report(
            reason="frozen_model_evaluation_failed",
            validation_errors=[f"frozen_model_evaluation_failed:{type(exc).__name__}"],
            database=database,
            features_file=features_file,
            model_file=model_file,
            report_file=report_file,
            write_report=write_report,
        )

    if model_evidence["compatibility_status"] != "compatible":
        warnings.append("sklearn_artifact_runtime_version_differs")
    split_frame, splits, split_evidence = assign_walkforward_test_folds(evaluation)
    if not splits:
        validation_errors.append("unable_to_build_walkforward_splits")
        scenario_rows: list[dict[str, Any]] = []
    else:
        scenario_rows = evaluate_scenario_matrix(split_frame)

    eligible = [row for row in scenario_rows if row["candidate_eligible"]]
    eligible.sort(
        key=lambda row: (
            -float(row["net_pnl"]),
            float(row["max_drawdown"]),
            str(row["scenario_id"]),
        )
    )
    selected = eligible[0] if eligible else None
    decision = "PAPER_CANDIDATE_READY" if selected else "CURRENT_MODEL_HAS_NO_USABLE_EDGE"
    status = "blocked" if validation_errors else "ok"
    reason = validation_errors[0] if validation_errors else decision.lower()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "decision": decision,
        "generated_at_utc": _utc_now(),
        "paper_db_path": str(database),
        "paper_db_sha256_before": source["sha256_before"],
        "paper_db_sha256_after": source["sha256_after"],
        "paper_db_hash_unchanged": source["sha256_before"] == source["sha256_after"],
        "closed_trade_count": source_summary["closed_trade_count"],
        "open_trade_count": source_summary["open_trade_count"],
        "accounting_eligible_count": int(accounting["accounting_valid"].sum()),
        "accounting_blocked_count": int((~accounting["accounting_valid"]).sum()),
        "market_features_path": str(features_file),
        "market_feature_rows": int(len(feature_rows)),
        "point_in_time_alignment": alignment,
        "model_path": str(model_file),
        "model_evidence": model_evidence,
        "evaluation_row_count": int(len(split_frame)),
        "walkforward": split_evidence,
        "scenario_count": len(scenario_rows),
        "scenarios": scenario_rows,
        "candidate_eligible": bool(selected),
        "selected_candidate": _candidate_config(selected),
        "baseline_current": baseline_current_metrics(closed),
        "drift_status": "not_used_as_authority",
        "shadow_status": "research_comparator_only",
        "paper_configuration_changed": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_path": str(report_file),
        "validation_errors": validation_errors,
        "warnings": warnings,
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }
    if write_report:
        report["write_performed"] = True
        _write_report(report_file, report)
    return report


def build_accounting_evaluation_frame(closed: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct both directional outcomes from entry/exit and observed costs."""

    frame = closed.copy()
    numeric_columns = (
        "open_rate",
        "close_rate",
        "amount",
        "contract_size",
        "leverage",
        "fee_open_cost",
        "fee_close_cost",
        "funding_fees",
        "close_profit_abs",
    )
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    complete = frame[list(numeric_columns)].notna().all(axis=1)
    complete &= frame["open_rate"].gt(0) & frame["close_rate"].gt(0)
    complete &= frame["amount"].gt(0) & frame["contract_size"].gt(0)
    fee = frame["fee_open_cost"].abs() * frame["leverage"] + frame[
        "fee_close_cost"
    ].abs()
    move = (frame["close_rate"] - frame["open_rate"]) * frame["amount"] * frame[
        "contract_size"
    ]
    frame["candidate_net_pnl_long"] = move - fee + frame["funding_fees"]
    frame["candidate_net_pnl_short"] = -move - fee + frame["funding_fees"]
    reconstructed = np.where(
        frame["side"].eq("SHORT"),
        frame["candidate_net_pnl_short"],
        frame["candidate_net_pnl_long"],
    )
    frame["accounting_residual"] = (reconstructed - frame["close_profit_abs"]).abs()
    frame["accounting_valid"] = complete & frame["accounting_residual"].le(
        ACCOUNTING_TOLERANCE_USDT
    )
    frame["actual_side"] = frame["side"].str.lower()
    frame["symbol"] = frame["pair"].map(_pair_to_symbol)
    frame["open_time_utc"] = frame["open_date"]
    frame["close_time_utc"] = frame["close_date"]
    frame["baseline_net_pnl"] = frame["close_profit_abs"]
    return frame


def align_features_point_in_time(
    trades: pd.DataFrame,
    market_features: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach only 5m candles whose close availability precedes entry."""

    features = market_features.copy()
    required = {"symbol", "ts", "tf"}
    missing = sorted(required.difference(features.columns))
    if missing:
        raise ValueError(f"market_features_missing_columns:{','.join(missing)}")
    features = features.loc[features["tf"].astype(str).eq("5m")].copy()
    features["symbol"] = features["symbol"].astype(str).map(_pair_to_symbol)
    features["ts"] = pd.to_datetime(features["ts"], utc=True, errors="coerce")
    features = features.dropna(subset=["ts", "symbol"])
    features["available_at_utc"] = features["ts"] + pd.Timedelta(minutes=5)
    features = features.sort_values(
        ["available_at_utc", "symbol", "ts"], kind="mergesort"
    ).reset_index(drop=True)
    eligible = trades.loc[trades["accounting_valid"]].copy()
    eligible = eligible.sort_values(
        ["open_time_utc", "symbol", "id"], kind="mergesort"
    ).reset_index(drop=True)
    aligned = pd.merge_asof(
        eligible,
        features,
        left_on="open_time_utc",
        right_on="available_at_utc",
        by="symbol",
        direction="backward",
        allow_exact_matches=True,
        suffixes=("", "_market"),
    )
    aligned["point_in_time_valid"] = aligned["available_at_utc"].notna() & aligned[
        "available_at_utc"
    ].le(aligned["open_time_utc"])
    valid_count = int(aligned["point_in_time_valid"].sum())
    return aligned, {
        "timeframe": "5m",
        "availability_rule": "available_at_utc=ts+5m<=open_time_utc",
        "candidate_trade_count": int(len(eligible)),
        "aligned_trade_count": valid_count,
        "blocked_trade_count": int(len(eligible) - valid_count),
        "lookahead_violation_count": int(
            (
                aligned["available_at_utc"].notna()
                & aligned["available_at_utc"].gt(aligned["open_time_utc"])
            ).sum()
        ),
    }


def score_with_frozen_model(
    aligned: pd.DataFrame,
    model_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    payload, compatibility = load_sklearn_artifact(model_path, strict=False)
    model = payload["model"]
    feature_columns = [str(value) for value in payload["feature_columns"]]
    metadata = payload.get("feature_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("model_feature_metadata_missing")
    trained_at = pd.to_datetime(payload.get("trained_at"), utc=True, errors="coerce")
    if pd.isna(trained_at):
        raise ValueError("model_trained_at_missing")
    missing = sorted(set(feature_columns).difference(aligned.columns))
    if missing:
        raise ValueError(f"model_feature_columns_missing:{','.join(missing)}")
    evaluation = aligned.loc[
        aligned["point_in_time_valid"] & aligned["open_time_utc"].ge(trained_at)
    ].copy()
    evaluation = evaluation.dropna(subset=feature_columns, how="all")
    if evaluation.empty:
        raise ValueError("no_post_model_point_in_time_rows")
    probability = model.predict_proba(_transform_features(evaluation, metadata))[:, 1]
    evaluation["prob_up"] = probability.astype(float)
    evaluation["score"] = (2.0 * evaluation["prob_up"]) - 1.0
    evaluation["confidence"] = (evaluation["prob_up"] - 0.5).abs()
    evaluation["market_regime"] = evaluation.get(
        "market_regime", pd.Series("unknown", index=evaluation.index)
    ).fillna("unknown")
    evaluation["market_regime_status"] = np.where(
        evaluation["market_regime"].astype(str).str.lower().eq("unknown"),
        "unknown",
        "point_in_time",
    )
    evaluation = evaluation.sort_values(
        ["open_time_utc", "close_time_utc", "id"], kind="mergesort"
    ).reset_index(drop=True)
    return evaluation, {
        "model_version": payload.get("model_version"),
        "model_backend": payload.get("model_backend"),
        "model_trained_at_utc": pd.Timestamp(trained_at).isoformat(),
        "model_sha256": _file_sha256(model_path),
        "feature_column_count": len(feature_columns),
        "compatibility_status": compatibility.status,
        "compatibility_reason": compatibility.reason,
        "paper_used_for_fit": False,
        "paper_used_for_model_calibration": False,
    }


def assign_walkforward_test_folds(
    evaluation: pd.DataFrame,
    *,
    embargo_seconds: int = 900,
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    frame = evaluation.sort_values(
        ["open_time_utc", "close_time_utc", "id"], kind="mergesort"
    ).reset_index(drop=True)
    splits = build_walkforward_splits(frame, embargo_seconds=embargo_seconds)
    frame["fold_id"] = 0
    for fold_number, split in enumerate(splits, start=1):
        frame.loc[split["_test_indices"], "fold_id"] = fold_number
    oos = frame.loc[frame["fold_id"].gt(0)].copy().reset_index(drop=True)
    return oos, splits, {
        "split_count": len(splits),
        "purging_applied": bool(splits),
        "embargo_applied": bool(splits),
        "embargo_seconds": embargo_seconds,
        "oos_row_count": int(len(oos)),
        "paper_used_for_fit": False,
        "paper_used_for_calibration": False,
        "paper_used_for_candidate_policy_evaluation": True,
    }


def evaluate_scenario_matrix(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        evaluate_one_scenario(
            frame,
            long_probability=long_probability,
            short_probability=short_probability,
            regime_gate_enabled=regime_gate_enabled,
            cooldown_minutes=cooldown_minutes,
        )
        for (
            long_probability,
            short_probability,
            regime_gate_enabled,
            cooldown_minutes,
        ) in SCENARIO_MATRIX
    ]


def evaluate_one_scenario(
    frame: pd.DataFrame,
    *,
    long_probability: float,
    short_probability: float,
    regime_gate_enabled: bool,
    cooldown_minutes: int,
) -> dict[str, Any]:
    ordered = frame.sort_values(
        ["open_time_utc", "close_time_utc", "id"], kind="mergesort"
    )
    cooldowns: dict[tuple[str, str], datetime] = {}
    pending_stop_events: list[tuple[datetime, tuple[str, str]]] = []
    executed: list[dict[str, Any]] = []
    no_trade_count = 0
    for row in ordered.to_dict("records"):
        observed_at = _timestamp(row["open_time_utc"])
        matured = [event for event in pending_stop_events if event[0] <= observed_at]
        pending_stop_events = [
            event for event in pending_stop_events if event[0] > observed_at
        ]
        for stopped_at, stopped_key in matured:
            cooldowns[stopped_key] = cooldown_deadline(
                stopped_at, cooldown_minutes
            )
        direction = decide_direction(
            row.get("prob_up"),
            long_probability=long_probability,
            short_probability=short_probability,
        )
        if direction.proposed_side == "no_trade":
            no_trade_count += 1
            continue
        key = (str(row["symbol"]), direction.proposed_side)
        policy = evaluate_candidate_policy(
            proposed_side=direction.proposed_side,
            market_regime=row.get("market_regime"),
            market_regime_status=row.get("market_regime_status"),
            regime_gate_enabled=regime_gate_enabled,
            observed_at=observed_at,
            cooldown_until=cooldowns.get(key),
        )
        if policy.final_decision != "ALLOW_CANDIDATE":
            no_trade_count += 1
            continue
        pnl_column = f"candidate_net_pnl_{direction.proposed_side}"
        pnl = _finite_float(row.get(pnl_column))
        if pnl is None:
            no_trade_count += 1
            continue
        executed.append({**row, "candidate_side": direction.proposed_side, "candidate_pnl": pnl})
        if (
            cooldown_minutes > 0
            and direction.proposed_side == str(row.get("actual_side"))
            and str(row.get("exit_reason") or "").lower() in STOP_REASONS
        ):
            pending_stop_events.append((_timestamp(row["close_time_utc"]), key))

    executed_frame = pd.DataFrame(executed)
    metrics = financial_metrics(
        executed_frame.get("candidate_pnl", pd.Series(dtype=float))
    )
    baseline = financial_metrics(ordered["baseline_net_pnl"])
    folds = _fold_metrics(executed_frame, ordered)
    candidate_eligible = (
        metrics.net_pnl > 0
        and metrics.expectancy > 0
        and metrics.profit_factor is not None
        and metrics.profit_factor > 1
        and folds["positive_fold_count"] > folds["negative_fold_count"]
        and metrics.max_drawdown < baseline.max_drawdown
    )
    return {
        "scenario_id": (
            f"p{int(long_probability * 100):02d}_{int(short_probability * 100):02d}"
            f"_regime_{'on' if regime_gate_enabled else 'off'}"
            f"_cooldown_{cooldown_minutes:02d}m"
        ),
        "long_probability": long_probability,
        "short_probability": short_probability,
        "regime_gate_enabled": regime_gate_enabled,
        "cooldown_minutes": cooldown_minutes,
        **asdict(metrics),
        "long_count": int(
            executed_frame.get("candidate_side", pd.Series(dtype=str)).eq("long").sum()
        ),
        "short_count": int(
            executed_frame.get("candidate_side", pd.Series(dtype=str)).eq("short").sum()
        ),
        "no_trade_count": int(no_trade_count),
        "stop_count": int(
            executed_frame.get("exit_reason", pd.Series(dtype=str))
            .astype(str)
            .str.lower()
            .isin(STOP_REASONS)
            .sum()
        ),
        "positive_fold_count": folds["positive_fold_count"],
        "negative_fold_count": folds["negative_fold_count"],
        "baseline_net_pnl": baseline.net_pnl,
        "baseline_max_drawdown": baseline.max_drawdown,
        "candidate_eligible": candidate_eligible,
    }


def financial_metrics(values: Iterable[object]) -> FinancialMetrics:
    pnl = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce")
    pnl = pnl.replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    trade_count = int(len(pnl))
    net_pnl = float(pnl.sum()) if trade_count else 0.0
    gross_profit = float(pnl.loc[pnl.gt(0)].sum()) if trade_count else 0.0
    gross_loss = abs(float(pnl.loc[pnl.lt(0)].sum())) if trade_count else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    equity = np.concatenate(([0.0], np.cumsum(pnl.to_numpy(dtype=float))))
    drawdown = np.maximum.accumulate(equity) - equity
    return FinancialMetrics(
        trade_count=trade_count,
        net_pnl=net_pnl,
        expectancy=net_pnl / trade_count if trade_count else 0.0,
        profit_factor=profit_factor,
        max_drawdown=float(drawdown.max()) if len(drawdown) else 0.0,
        win_rate=float(pnl.gt(0).mean()) if trade_count else 0.0,
    )


def baseline_current_metrics(closed: pd.DataFrame) -> dict[str, Any]:
    metrics = financial_metrics(closed["close_profit_abs"])
    return asdict(metrics)


def _fold_metrics(executed: pd.DataFrame, source: pd.DataFrame) -> dict[str, int]:
    pnl_by_fold = {
        int(fold): 0.0 for fold in sorted(source["fold_id"].astype(int).unique())
    }
    if not executed.empty:
        grouped = executed.groupby("fold_id", sort=True)["candidate_pnl"].sum()
        for fold, pnl in grouped.items():
            pnl_by_fold[int(fold)] = float(pnl)
    return {
        "positive_fold_count": sum(value > 0 for value in pnl_by_fold.values()),
        "negative_fold_count": sum(value <= 0 for value in pnl_by_fold.values()),
    }


def _candidate_config(selected: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if selected is None:
        return None
    return {
        "long_probability": selected["long_probability"],
        "short_probability": selected["short_probability"],
        "regime_gate_enabled": selected["regime_gate_enabled"],
        "cooldown_minutes": selected["cooldown_minutes"],
        "automatic_application_allowed": False,
        "human_review_required": True,
    }


def _blocked_report(
    *,
    reason: str,
    validation_errors: list[str],
    database: Path,
    features_file: Path,
    model_file: Path,
    report_file: Path,
    write_report: bool,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked",
        "reason": reason,
        "decision": "CURRENT_MODEL_HAS_NO_USABLE_EDGE",
        "generated_at_utc": _utc_now(),
        "paper_db_path": str(database),
        "market_features_path": str(features_file),
        "model_path": str(model_file),
        "scenario_count": 0,
        "scenarios": [],
        "candidate_eligible": False,
        "selected_candidate": None,
        "paper_configuration_changed": False,
        "write_requested": bool(write_report),
        "write_performed": False,
        "output_path": str(report_file),
        "validation_errors": validation_errors,
        "warnings": [],
        "safety_flags": dict(SAFETY_FLAGS),
        **SAFETY_FLAGS,
    }


def _pair_to_symbol(value: object) -> str:
    return str(value or "").upper().replace("/", "").replace(":USDT", "")


def _resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _timestamp(value: object) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
