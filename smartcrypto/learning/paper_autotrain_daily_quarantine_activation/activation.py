"""Daily paper auto-training runner for research quarantine.

This module performs only quarantine/research writes. It does not import
Freqtrade, ccxt, RiskManager, Docker tooling, or any runtime signal producer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "paper_autotrain_daily_quarantine_activation_v1"
DECISION_QUARANTINE = "QUARANTINE_ONLY"

DEFAULT_CLOSED_TRADES_PATH = Path("data/feedback/paper_closed_trades_incremental.parquet")
DEFAULT_CLOSED_TRADES_CSV = Path("data/trades/inbox/freqtrade_paper_closed_trades.csv")
DEFAULT_MICROBATCH_PATH = Path("data/features/incremental_training_microbatch.parquet")
DEFAULT_REPORT_JSON = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.json")
DEFAULT_REPORT_MD = Path("data/reports/paper_autotrain_daily_quarantine_activation_v1.md")
DEFAULT_RESEARCH_DIR = Path("data/research/paper_autotrain_daily_quarantine")
DEFAULT_MODEL_DIR = Path("data/models/quarantine/paper_autotrain")
DEFAULT_REGISTRY_PATH = Path("data/registries/quarantine/paper_autotrain_candidate_registry_v1.json")
DEFAULT_FEEDBACK_EVENTS_PATH = Path("data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl")

ALLOWED_WRITE_ROOTS = (
    Path("data/feedback"),
    Path("data/reports"),
    Path("data/research/paper_autotrain_daily_quarantine"),
    Path("data/models/quarantine/paper_autotrain"),
    Path("data/registries/quarantine"),
)


@dataclass(frozen=True)
class QuarantinePaths:
    report_json: Path
    report_markdown: Path
    research_dir: Path
    model_dir: Path
    registry_path: Path
    feedback_events_path: Path
    microbatch_snapshot_path: Path
    last_run_state_path: Path


def build_paper_autotrain_daily_quarantine_activation_v1(
    *,
    project_root: str | Path,
    once: bool = False,
    write_feedback: bool = False,
    train_challenger: bool = False,
    write_quarantine_artifacts: bool = False,
    write_report: bool = False,
    dry_run: bool = False,
    scheduler_check: bool = False,
    fail_on_operational_write: bool = False,
    output_json_path: str | Path | None = None,
    output_markdown_path: str | Path | None = None,
    generated_at_utc: str | None = None,
    closed_trades_frame: pd.DataFrame | None = None,
    microbatch_frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build and optionally execute the quarantine auto-training cycle."""

    root = Path(project_root).resolve()
    generated_at = generated_at_utc or datetime.now(UTC).isoformat()
    run_id = make_run_id(generated_at)
    paths = build_paths(root, run_id, output_json_path, output_markdown_path)
    requested_writes = bool(write_feedback or write_quarantine_artifacts or write_report)
    validation_errors = validate_write_boundaries(root, paths) if fail_on_operational_write or requested_writes else []
    scheduler_payload = scheduler_check_report(root)

    closed_trades, closed_reason = load_closed_trades(root, closed_trades_frame)
    normalized_closed = normalize_closed_trades(closed_trades)
    microbatch, microbatch_reason = load_microbatch(root, microbatch_frame)
    prepared_microbatch = prepare_microbatch(microbatch)

    blockers: list[str] = list(validation_errors)
    warnings: list[str] = []
    if closed_trades.empty:
        blockers.append(closed_reason)
    if normalized_closed.empty:
        blockers.append("no_valid_closed_trades")
    if train_challenger and microbatch.empty:
        blockers.append(microbatch_reason)
    if train_challenger and prepared_microbatch.empty:
        blockers.append("empty_microbatch")
    if scheduler_check and scheduler_payload["status"] != "ok":
        warnings.append("scheduler_check_not_ready")

    qlib_result = challenger_not_requested("qlib")
    ai_shadow_result = challenger_not_requested("ai_shadow")
    if train_challenger and not prepared_microbatch.empty:
        qlib_result = train_quarantine_challenger(
            root=root,
            run_id=run_id,
            backend_id="qlib",
            backend_available=importlib.util.find_spec("qlib") is not None,
            backend_unavailable_reason="qlib_backend_unavailable",
            microbatch=prepared_microbatch,
            paths=paths,
            write_artifact=write_quarantine_artifacts,
        )
        ai_shadow_result = train_quarantine_challenger(
            root=root,
            run_id=run_id,
            backend_id="ai_shadow",
            backend_available=sklearn_available(),
            backend_unavailable_reason="ai_shadow_backend_unavailable",
            microbatch=prepared_microbatch,
            paths=paths,
            write_artifact=write_quarantine_artifacts,
        )
        warnings.extend(qlib_result["warnings"])
        warnings.extend(ai_shadow_result["warnings"])
        blockers.extend(qlib_result["blockers"])
        blockers.extend(ai_shadow_result["blockers"])

    write_results = write_quarantine_outputs(
        root=root,
        paths=paths,
        run_id=run_id,
        generated_at_utc=generated_at,
        normalized_closed=normalized_closed,
        prepared_microbatch=prepared_microbatch,
        qlib_result=qlib_result,
        ai_shadow_result=ai_shadow_result,
        write_feedback=write_feedback,
        write_quarantine_artifacts=write_quarantine_artifacts,
        write_report=False,
        report=None,
    )
    if requested_writes and validation_errors:
        blockers.append("write_boundary_validation_failed")

    status, reason = decide_status(
        once=once,
        scheduler_check=scheduler_check,
        requested_writes=requested_writes,
        train_challenger=train_challenger,
        blockers=blockers,
        normalized_closed=normalized_closed,
        prepared_microbatch=prepared_microbatch,
    )
    safety = safety_flags(
        writes_quarantine_registry=bool(write_results["quarantine_registry_written"]),
        writes_quarantined_model_artifacts=bool(
            qlib_result["artifact_written"] or ai_shadow_result["artifact_written"]
        ),
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "project_root": str(root),
        "status": status,
        "reason": reason,
        "decision": DECISION_QUARANTINE,
        "run_id": run_id,
        "run_mode": run_mode(once=once, scheduler_check=scheduler_check, dry_run=dry_run),
        "daily_autotrain_quarantine_enabled": bool(once and not dry_run),
        "once_requested": bool(once),
        "scheduler_check_requested": bool(scheduler_check),
        "write_feedback_requested": bool(write_feedback),
        "train_challenger_requested": bool(train_challenger),
        "write_quarantine_artifacts_requested": bool(write_quarantine_artifacts),
        "write_report_requested": bool(write_report),
        "closed_trades_loaded_count": int(len(closed_trades)),
        "closed_trades_valid_count": int(len(normalized_closed)),
        "feedback_events_count": int(len(normalized_closed)),
        "microbatch_rows": int(len(prepared_microbatch)),
        "feature_count": len(feature_columns(prepared_microbatch)),
        "label_count": label_count(prepared_microbatch),
        "qlib_challenger_train_status": qlib_result["status"],
        "ai_shadow_challenger_train_status": ai_shadow_result["status"],
        "qlib_candidate_artifact_path": qlib_result["artifact_path"],
        "ai_shadow_candidate_artifact_path": ai_shadow_result["artifact_path"],
        "quarantine_registry_path": str(paths.registry_path),
        "quarantine_candidate_count": int(write_results["quarantine_candidate_count"]),
        "promoted_candidate_count": 0,
        "active_model_changed": False,
        "model_promotion_performed": False,
        "active_registry_changed": False,
        "runtime_updated": False,
        "active_signal_file_written": False,
        "paper_selector_runtime_enabled": False,
        "sends_orders": False,
        "changes_risk": False,
        "blockers": sorted_unique(blockers),
        "warnings": sorted_unique(warnings),
        "output_paths": {
            "report_json": str(paths.report_json),
            "report_markdown": str(paths.report_markdown),
            "feedback_events": str(paths.feedback_events_path) if write_feedback else None,
            "microbatch_snapshot": str(paths.microbatch_snapshot_path) if write_quarantine_artifacts else None,
            "last_run_state": str(paths.last_run_state_path) if write_quarantine_artifacts else None,
            "quarantine_registry": str(paths.registry_path) if write_quarantine_artifacts else None,
            "quarantine_model_dir": str(paths.model_dir) if write_quarantine_artifacts else None,
        },
        "scheduler_check": scheduler_payload,
        "closed_trades_source_reason": closed_reason,
        "microbatch_source_reason": microbatch_reason,
        "qlib_challenger_summary": qlib_result,
        "ai_shadow_challenger_summary": ai_shadow_result,
        **safety,
        "safety_flags": safety,
        "write_performed": bool(write_results["write_performed"]),
    }
    if write_report:
        paths.report_json.parent.mkdir(parents=True, exist_ok=True)
        paths.report_markdown.parent.mkdir(parents=True, exist_ok=True)
        write_json(paths.report_json, report)
        paths.report_markdown.write_text(render_markdown(report), encoding="utf-8")
        report["write_performed"] = True
    if write_quarantine_artifacts:
        write_last_run_state(paths.last_run_state_path, report)
    return report


def load_closed_trades(root: Path, frame: pd.DataFrame | None) -> tuple[pd.DataFrame, str]:
    if frame is not None:
        return frame.copy(), "in_memory"
    parquet_path = root / DEFAULT_CLOSED_TRADES_PATH
    csv_path = root / DEFAULT_CLOSED_TRADES_CSV
    if parquet_path.exists():
        return pd.read_parquet(parquet_path), "feedback_parquet"
    if csv_path.exists():
        return pd.read_csv(csv_path), "closed_trades_csv"
    return pd.DataFrame(), "missing_closed_trades"


def load_microbatch(root: Path, frame: pd.DataFrame | None) -> tuple[pd.DataFrame, str]:
    if frame is not None:
        return frame.copy(), "in_memory"
    path = root / DEFAULT_MICROBATCH_PATH
    if path.exists():
        return pd.read_parquet(path), "incremental_microbatch"
    return pd.DataFrame(), "missing_microbatch"


def normalize_closed_trades(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    output = pd.DataFrame()
    output["order_id"] = pick_series(frame, ("order_id", "trade_id", "id")).astype(str).fillna("")
    output["symbol"] = pick_series(frame, ("symbol", "moeda", "pair")).map(normalize_symbol)
    output["side"] = pick_series(frame, ("side", "fechar_side", "direction")).map(normalize_side)
    output["open_time_utc"] = pd.to_datetime(
        pick_series(frame, ("open_time_utc", "horario_abertura", "open_date", "open_time")),
        utc=True,
        errors="coerce",
    )
    output["close_time_utc"] = pd.to_datetime(
        pick_series(frame, ("close_time_utc", "horario_fechamento", "close_date", "close_time")),
        utc=True,
        errors="coerce",
    )
    output["net_pnl"] = pd.to_numeric(
        pick_series(frame, ("net_pnl", "pnl_fechado", "profit_abs", "pnl_usdt", "pnl")),
        errors="coerce",
    )
    valid = (
        output["symbol"].ne("")
        & output["side"].ne("")
        & output["open_time_utc"].notna()
        & output["close_time_utc"].notna()
        & output["net_pnl"].notna()
        & (output["close_time_utc"] >= output["open_time_utc"])
    )
    return output.loc[valid].reset_index(drop=True)


def prepare_microbatch(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "target_profitable" not in frame.columns:
        return pd.DataFrame()
    result = frame.copy()
    features = feature_columns(result)
    if not features:
        return pd.DataFrame()
    result["target_profitable"] = pd.to_numeric(result["target_profitable"], errors="coerce")
    result = result.loc[result["target_profitable"].isin([0, 1])].copy()
    for column in features:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["target_profitable"]).reset_index(drop=True)


def train_quarantine_challenger(
    *,
    root: Path,
    run_id: str,
    backend_id: str,
    backend_available: bool,
    backend_unavailable_reason: str,
    microbatch: pd.DataFrame,
    paths: QuarantinePaths,
    write_artifact: bool,
) -> dict[str, Any]:
    if not backend_available:
        return challenger_unavailable(backend_id, backend_unavailable_reason)
    features = feature_columns(microbatch)
    if not features:
        return challenger_blocked(backend_id, "missing_feature_columns")
    if microbatch["target_profitable"].nunique(dropna=True) < 2:
        return challenger_blocked(backend_id, "single_class_target")
    try:
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return challenger_unavailable(backend_id, f"{backend_id}_sklearn_backend_unavailable")

    x_train = microbatch[features]
    y_train = microbatch["target_profitable"].astype(int)
    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=500, random_state=17)),
        ]
    )
    model.fit(x_train, y_train)
    classifier = model.named_steps["classifier"]
    probabilities = model.predict_proba(x_train)[:, 1]
    candidate = {
        "candidate_id": f"{backend_id}_{run_id}",
        "backend_id": backend_id,
        "status": "trained_quarantine_only",
        "row_count": int(len(microbatch)),
        "feature_count": int(len(features)),
        "class_balance": {
            str(key): int(value)
            for key, value in y_train.value_counts().sort_index().to_dict().items()
        },
        "mean_probability": round(float(probabilities.mean()), 10),
        "coefficients": classifier.coef_.tolist(),
        "intercept": classifier.intercept_.tolist(),
        "classes": [int(value) for value in classifier.classes_],
        "promotion_eligible": False,
        "quarantine_only": True,
    }
    artifact_path: str | None = None
    artifact_hash: str | None = None
    if write_artifact:
        model_dir = paths.model_dir / run_id
        model_dir.mkdir(parents=True, exist_ok=True)
        artifact = model_dir / f"{backend_id}_candidate_model.json"
        write_json(artifact, candidate)
        artifact_path = str(artifact)
        artifact_hash = file_sha256(artifact)
    return {
        "backend_id": backend_id,
        "status": "trained_quarantine_only",
        "reason": "trained_quarantine_only",
        "artifact_path": artifact_path,
        "artifact_hash": artifact_hash,
        "artifact_written": bool(artifact_path),
        "candidate": candidate,
        "blockers": [],
        "warnings": [],
    }


def write_quarantine_outputs(
    *,
    root: Path,
    paths: QuarantinePaths,
    run_id: str,
    generated_at_utc: str,
    normalized_closed: pd.DataFrame,
    prepared_microbatch: pd.DataFrame,
    qlib_result: Mapping[str, Any],
    ai_shadow_result: Mapping[str, Any],
    write_feedback: bool,
    write_quarantine_artifacts: bool,
    write_report: bool,
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    write_performed = False
    if write_feedback:
        paths.feedback_events_path.parent.mkdir(parents=True, exist_ok=True)
        paths.feedback_events_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in frame_records(normalized_closed))
            + ("\n" if len(normalized_closed) else ""),
            encoding="utf-8",
        )
        write_performed = True
    candidate_rows = [
        result.get("candidate")
        for result in (qlib_result, ai_shadow_result)
        if isinstance(result.get("candidate"), Mapping)
    ]
    if write_quarantine_artifacts:
        paths.research_dir.mkdir(parents=True, exist_ok=True)
        if not prepared_microbatch.empty:
            prepared_microbatch.to_parquet(paths.microbatch_snapshot_path, index=False)
        registry_payload = {
            "schema_version": "paper_autotrain_quarantine_candidate_registry_v1",
            "generated_at_utc": generated_at_utc,
            "run_id": run_id,
            "candidate_count": len(candidate_rows),
            "promoted_candidate_count": 0,
            "active_registry_changed": False,
            "quarantine_only": True,
            "candidates": candidate_rows,
        }
        write_json(paths.registry_path, registry_payload)
        write_performed = True
    if write_report and report is not None:
        write_json(paths.report_json, report)
        paths.report_markdown.write_text(render_markdown(report), encoding="utf-8")
        write_performed = True
    return {
        "write_performed": write_performed,
        "quarantine_registry_written": bool(write_quarantine_artifacts),
        "quarantine_candidate_count": len(candidate_rows),
    }


def scheduler_check_report(root: Path) -> dict[str, Any]:
    scheduler_script = root / "scripts" / "run_paper_autolearning_scheduler_v1.py"
    return {
        "status": "ok" if scheduler_script.exists() else "warning",
        "reason": "scheduler_runner_available" if scheduler_script.exists() else "scheduler_runner_missing",
        "runner_path": str(scheduler_script),
        "runner_exists": scheduler_script.exists(),
        "registers_scheduler": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
        "creates_service": False,
        "starts_service": False,
        "touches_freqtrade": False,
        "touches_risk_manager": False,
        "touches_runtime": False,
        "allowed_future_command": [
            "python",
            "scripts/run_paper_autotrain_daily_quarantine_activation_v1.py",
            "--project-root",
            str(root),
            "--once",
            "--write-feedback",
            "--train-challenger",
            "--write-quarantine-artifacts",
            "--write-report",
            "--json",
        ],
    }


def decide_status(
    *,
    once: bool,
    scheduler_check: bool,
    requested_writes: bool,
    train_challenger: bool,
    blockers: Sequence[str],
    normalized_closed: pd.DataFrame,
    prepared_microbatch: pd.DataFrame,
) -> tuple[str, str]:
    blocking = sorted_unique(blockers)
    if "missing_closed_trades" in blocking or "no_valid_closed_trades" in blocking:
        return "blocked", "missing_or_invalid_closed_trades"
    if train_challenger and ("missing_microbatch" in blocking or "empty_microbatch" in blocking):
        return "blocked", "missing_or_empty_microbatch"
    hard_training = [item for item in blocking if item.endswith("_unavailable") or item == "single_class_target"]
    if hard_training and not prepared_microbatch.empty:
        return "warning", "quarantine_cycle_executed_with_backend_warnings"
    if blocking:
        return "blocked", blocking[0]
    if scheduler_check and not once and not requested_writes:
        return "ok", "scheduler_check_ok"
    if once and requested_writes:
        return "ok", "quarantine_cycle_executed"
    if normalized_closed.empty:
        return "blocked", "missing_or_invalid_closed_trades"
    return "planned", "dry_run_plan_only"


def safety_flags(
    *,
    writes_quarantine_registry: bool = False,
    writes_quarantined_model_artifacts: bool = False,
) -> dict[str, bool]:
    return {
        "research_only": True,
        "paper_only": True,
        "shadow_only": True,
        "quarantine_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_active_model": False,
        "active_model_changed": False,
        "model_promotion_performed": False,
        "promotes_model": False,
        "active_registry_changed": False,
        "writes_active_registry": False,
        "writes_quarantine_registry": bool(writes_quarantine_registry),
        "writes_quarantined_model_artifacts": bool(writes_quarantined_model_artifacts),
        "writes_active_model_artifact": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_thresholds": False,
        "updates_freqtrade": False,
        "updates_freqtrade_strategy": False,
        "updates_freqtrade_config": False,
        "updates_risk_manager": False,
        "paper_selector_runtime_enabled": False,
        "writes_signal_file": False,
        "writes_active_freqtrade_signals": False,
        "active_signal_file_written": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "scheduler_registered": False,
        "starts_service": False,
        "creates_cron": False,
        "creates_systemd_timer": False,
        "creates_windows_task": False,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Paper Autotrain Daily Quarantine Activation V1",
            "",
            f"- Status: `{report.get('status')}`",
            f"- Reason: `{report.get('reason')}`",
            f"- Decision: `{report.get('decision')}`",
            f"- Run id: `{report.get('run_id')}`",
            f"- Closed trades valid: `{report.get('closed_trades_valid_count')}`",
            f"- Microbatch rows: `{report.get('microbatch_rows')}`",
            f"- Qlib challenger: `{report.get('qlib_challenger_train_status')}`",
            f"- AI Shadow challenger: `{report.get('ai_shadow_challenger_train_status')}`",
            f"- Quarantine candidates: `{report.get('quarantine_candidate_count')}`",
            f"- Promoted candidates: `{report.get('promoted_candidate_count')}`",
            "",
            "This runner executes research/quarantine learning only. It does not promote models, update runtime, write active signals, alter Freqtrade/RiskManager, access private exchange APIs, or send orders.",
            "",
        ]
    )


def write_last_run_state(path: Path, report: Mapping[str, Any]) -> None:
    payload = {
        "schema_version": "paper_autotrain_daily_quarantine_last_run_state_v1",
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "reason": report.get("reason"),
        "generated_at_utc": report.get("generated_at_utc"),
        "decision": report.get("decision"),
        "quarantine_only": True,
        "active_model_changed": False,
        "runtime_updated": False,
        "sends_orders": False,
    }
    write_json(path, payload)


def validate_write_boundaries(root: Path, paths: QuarantinePaths) -> list[str]:
    errors: list[str] = []
    for path in (
        paths.report_json,
        paths.report_markdown,
        paths.research_dir,
        paths.model_dir,
        paths.registry_path,
        paths.feedback_events_path,
        paths.microbatch_snapshot_path,
        paths.last_run_state_path,
    ):
        if not is_allowed_write_path(root, path):
            errors.append(f"write_path_outside_allowed_roots:{path}")
    return sorted_unique(errors)


def is_allowed_write_path(root: Path, path: Path) -> bool:
    resolved = path.resolve()
    for allowed in ALLOWED_WRITE_ROOTS:
        try:
            resolved.relative_to((root / allowed).resolve())
            return True
        except ValueError:
            continue
    return False


def build_paths(
    root: Path,
    run_id: str,
    output_json_path: str | Path | None,
    output_markdown_path: str | Path | None,
) -> QuarantinePaths:
    research_dir = root / DEFAULT_RESEARCH_DIR / run_id
    return QuarantinePaths(
        report_json=resolve(root, output_json_path, DEFAULT_REPORT_JSON),
        report_markdown=resolve(root, output_markdown_path, DEFAULT_REPORT_MD),
        research_dir=research_dir,
        model_dir=root / DEFAULT_MODEL_DIR,
        registry_path=root / DEFAULT_REGISTRY_PATH,
        feedback_events_path=root / DEFAULT_FEEDBACK_EVENTS_PATH,
        microbatch_snapshot_path=research_dir / "incremental_training_microbatch.parquet",
        last_run_state_path=root / DEFAULT_RESEARCH_DIR / "last_run_state.json",
    )


def challenger_not_requested(backend_id: str) -> dict[str, Any]:
    return {
        "backend_id": backend_id,
        "status": "not_requested",
        "reason": "train_challenger_not_requested",
        "artifact_path": None,
        "artifact_hash": None,
        "artifact_written": False,
        "candidate": None,
        "blockers": [],
        "warnings": [],
    }


def challenger_unavailable(backend_id: str, reason: str) -> dict[str, Any]:
    return {
        "backend_id": backend_id,
        "status": "unavailable",
        "reason": reason,
        "artifact_path": None,
        "artifact_hash": None,
        "artifact_written": False,
        "candidate": None,
        "blockers": [reason],
        "warnings": [reason],
    }


def challenger_blocked(backend_id: str, reason: str) -> dict[str, Any]:
    return {
        "backend_id": backend_id,
        "status": "blocked",
        "reason": reason,
        "artifact_path": None,
        "artifact_hash": None,
        "artifact_written": False,
        "candidate": None,
        "blockers": [reason],
        "warnings": [],
    }


def sklearn_available() -> bool:
    return all(
        importlib.util.find_spec(module) is not None
        for module in (
            "sklearn.impute",
            "sklearn.linear_model",
            "sklearn.pipeline",
            "sklearn.preprocessing",
        )
    )


def feature_columns(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    return [
        str(column)
        for column in frame.columns
        if str(column).startswith("feature_")
        and pd.to_numeric(frame[column], errors="coerce").notna().any()
    ]


def label_count(frame: pd.DataFrame) -> int:
    return sum(1 for column in ("target_profitable", "target_return") if column in frame.columns)


def pick_series(frame: pd.DataFrame, candidates: Sequence[str]) -> pd.Series:
    lookup = {str(column).lower(): column for column in frame.columns}
    for candidate in candidates:
        column = lookup.get(candidate.lower())
        if column is not None:
            return frame[column]
    return pd.Series([""] * len(frame), index=frame.index)


def normalize_symbol(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper().replace("/", "").replace(":", "").replace("_", "")


def normalize_side(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if "short" in text or "sell" in text:
        return "short"
    if "long" in text or "buy" in text:
        return "long"
    return text


def run_mode(*, once: bool, scheduler_check: bool, dry_run: bool) -> str:
    if scheduler_check:
        return "scheduler_check"
    if once:
        return "once_dry_run" if dry_run else "once"
    return "plan"


def make_run_id(generated_at_utc: str) -> str:
    safe = generated_at_utc.replace(":", "").replace("+", "Z")
    digest = hashlib.sha256(generated_at_utc.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso")) if not frame.empty else []


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=json_safe) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else root / path


def sorted_unique(values: Sequence[Any]) -> list[str]:
    return sorted({str(value) for value in values if str(value).strip()})


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value
