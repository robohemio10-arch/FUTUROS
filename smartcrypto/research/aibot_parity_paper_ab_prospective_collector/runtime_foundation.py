"""Runtime-activation foundation for prospective AIBOT Paper A/B evidence.

This module provides one-cycle execution, locking, heartbeat and health evidence.
It deliberately does not register a scheduler, start the collection clock, split
Paper traffic, publish signals, or acquire operational authority.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.research.aibot_parity_paper_ab_soak import load_preregistration
from smartcrypto.research.paper_ab_edge_selector.persistence import (
    resolve_assignments_path,
    resolve_report_path,
    write_assignments_idempotent,
    write_report,
)
from smartcrypto.runtime.integrity_traceability_v2 import (
    AtomicWritePolicy,
    atomic_write_json,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    _InterProcessFileLock,
)

from .collector import (
    DEFAULT_OBSERVATIONS,
    OBSERVATION_SCHEMA_VERSION,
    SAFETY_FLAGS,
    CollectionResult,
    collect_prospective_evidence,
    immutable_assignment_rows,
    load_aibot_snapshots,
    load_decision_ledger_jsonl,
    load_normalized_closed_trades,
    read_observation_ledger,
    write_observations_idempotent,
)
from .financial_fingerprint import (
    DEFAULT_PAPER_CONFIG,
    DEFAULT_STRATEGY_FILE,
    DEFAULT_STRATEGY_NAME,
    build_paper_financial_config_fingerprint,
)

SCHEMA_VERSION = "aibot_parity_prospective_runtime_foundation_v1"
HEARTBEAT_SCHEMA_VERSION = "aibot_parity_prospective_runtime_heartbeat_v1"
HEALTH_SCHEMA_VERSION = "aibot_parity_prospective_runtime_health_v1"
DEFAULT_RUNTIME_CONFIG = Path(
    "config/research/aibot_parity_prospective_runtime_foundation_v1.json"
)
DEFAULT_PREREGISTRATION = Path("config/research/aibot_parity_paper_ab_soak_v1.json")
DEFAULT_ASSIGNMENTS = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_soak_assignments_v1.jsonl"
)
DEFAULT_COLLECTOR_REPORT = Path(
    "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_collector_v1.json"
)
DEFAULT_HEARTBEAT = Path(
    "data/reports/aibot_parity/aibot_parity_prospective_runtime_heartbeat_v1.json"
)
DEFAULT_HEALTH = Path(
    "data/reports/aibot_parity/aibot_parity_prospective_runtime_health_v1.json"
)
DEFAULT_LOCK = Path(
    "data/reports/aibot_parity/.aibot_parity_prospective_runtime_foundation.lock"
)

RUNTIME_SAFETY_FLAGS: dict[str, bool] = {
    **SAFETY_FLAGS,
    "live": False,
    "canary": False,
    "real_order_submission": False,
    "scheduler_registration_performed": False,
    "recurring_collection_proven": False,
}

_REQUIRED_CONFIG_FALSE = (
    "operational_authority",
    "traffic_split_performed",
    "treatment_runtime_assignment_performed",
    "paper_behavior_changed",
    "writes_active_signals",
    "signal_published",
    "sends_orders",
    "exchange_private_access",
    "changes_strategy",
    "changes_risk",
    "changes_stake",
    "changes_leverage",
    "changes_roi",
    "changes_stoploss",
    "changes_universe",
    "changes_model",
    "paper_treatment_release_allowed",
    "paper_activation_performed",
    "qlib_security_gate_bypassed",
    "collection_clock_started",
    "prospective_collection_running_proven",
    "live",
    "canary",
    "real_order_submission",
)
_REQUIRED_CONFIG_TRUE = ("paper_only", "shadow_only", "research_only")


@dataclass(frozen=True)
class RuntimeFoundationConfig:
    runner_id: str
    expected_financial_config_sha256: str
    paper_config_path: str
    strategy_path: str
    strategy_name: str
    max_snapshot_age_seconds: float
    lock_timeout_seconds: float
    heartbeat_path: str
    health_path: str
    deployment_foundation: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class RuntimeCycleResult:
    report: dict[str, Any]
    collector_result: CollectionResult | None
    heartbeat: dict[str, Any]
    health: dict[str, Any]


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field}_missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return parsed.astimezone(UTC)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_mapping(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"json_object_required:{path}")
    return dict(payload)


def load_runtime_foundation_config(
    *, project_root: str | Path, path: str | Path = DEFAULT_RUNTIME_CONFIG
) -> RuntimeFoundationConfig:
    root = Path(project_root).resolve()
    config_path = _resolve(root, path)
    payload = _read_json_mapping(config_path)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("runtime_foundation_schema_version_invalid")
    for key in _REQUIRED_CONFIG_TRUE:
        if payload.get(key) is not True:
            raise ValueError(f"runtime_foundation_{key}_must_be_true")
    for key in _REQUIRED_CONFIG_FALSE:
        if payload.get(key) is not False:
            raise ValueError(f"runtime_foundation_{key}_must_be_false")
    runner_id = str(payload.get("runner_id") or "").strip()
    if not runner_id:
        raise ValueError("runtime_foundation_runner_id_required")
    max_snapshot_age_seconds = float(payload.get("max_snapshot_age_seconds", 900))
    lock_timeout_seconds = float(payload.get("lock_timeout_seconds", 5.0))
    if max_snapshot_age_seconds <= 0:
        raise ValueError("max_snapshot_age_seconds_must_be_positive")
    if lock_timeout_seconds <= 0:
        raise ValueError("lock_timeout_seconds_must_be_positive")
    expected = str(payload.get("expected_paper_financial_config_sha256") or "").strip()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("expected_paper_financial_config_sha256_invalid")
    heartbeat_value = str(payload.get("heartbeat_path") or DEFAULT_HEARTBEAT)
    health_value = str(payload.get("health_path") or DEFAULT_HEALTH)
    allowed_reports = (root / "data" / "reports" / "aibot_parity").resolve()
    for label, value in (("heartbeat", heartbeat_value), ("health", health_value)):
        target = _resolve(root, value)
        try:
            target.relative_to(allowed_reports)
        except ValueError as exc:
            raise ValueError(f"runtime_foundation_{label}_path_outside_reports") from exc
        if target.suffix.lower() != ".json":
            raise ValueError(f"runtime_foundation_{label}_path_must_be_json")

    deployment = payload.get("deployment_foundation")
    if not isinstance(deployment, Mapping):
        raise ValueError("deployment_foundation_must_be_object")
    if deployment.get("scheduler_registration_performed") is not False:
        raise ValueError("scheduler_registration_performed_must_be_false")
    if deployment.get("recurring_collection_proven") is not False:
        raise ValueError("recurring_collection_proven_must_be_false")
    if deployment.get("recurring_runner_available") is not True:
        raise ValueError("recurring_runner_available_must_be_true")

    return RuntimeFoundationConfig(
        runner_id=runner_id,
        expected_financial_config_sha256=expected,
        paper_config_path=str(payload.get("paper_config_path") or DEFAULT_PAPER_CONFIG),
        strategy_path=str(payload.get("strategy_path") or DEFAULT_STRATEGY_FILE),
        strategy_name=str(payload.get("strategy_name") or DEFAULT_STRATEGY_NAME),
        max_snapshot_age_seconds=max_snapshot_age_seconds,
        lock_timeout_seconds=lock_timeout_seconds,
        heartbeat_path=heartbeat_value,
        health_path=health_value,
        deployment_foundation=dict(deployment),
        raw=payload,
    )


def _snapshot_freshness(
    snapshots: list[Any], *, now_utc: datetime, max_age_seconds: float
) -> dict[str, Any]:
    if not snapshots:
        return {
            "status": "not_requested",
            "reason": "snapshot_not_requested",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    latest = max(snapshot.created_at_utc.astimezone(UTC) for snapshot in snapshots)
    age_seconds = (now_utc - latest).total_seconds()
    if age_seconds < -60:
        status = "blocked"
        reason = "snapshot_created_at_in_future"
    elif age_seconds > max_age_seconds:
        status = "blocked"
        reason = "snapshot_stale"
    else:
        status = "ok"
        reason = "snapshot_fresh"
    return {
        "status": status,
        "reason": reason,
        "latest_created_at_utc": _iso(latest),
        "age_seconds": float(age_seconds),
        "max_age_seconds": float(max_age_seconds),
    }


def _read_prior_heartbeat(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ValueError("heartbeat_not_regular_file")
    return _read_json_mapping(path)


def _last_persisted_v2_observation(observations_path: Path) -> str | None:
    """Return the latest persisted V2 capture timestamp, never legacy V1 evidence."""

    captured_values: list[datetime] = []
    for row in read_observation_ledger(observations_path):
        if row.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
            continue
        captured_values.append(
            _parse_utc(row.get("captured_at_utc"), field="captured_at_utc")
        )
    if not captured_values:
        return None
    return _iso(max(captured_values))


def _build_heartbeat(
    *,
    config: RuntimeFoundationConfig,
    collector_run_id: str,
    started_at: datetime,
    finished_at: datetime,
    collector_status: str,
    collector_reason: str,
    prior: Mapping[str, Any],
    source_freshness: Mapping[str, Any],
    financial_config_sha256: str | None,
    observations_path: Path,
    decision_ledger_path: Path,
    closed_trades_path: Path,
) -> dict[str, Any]:
    prior_sequence = int(prior.get("sequence") or 0)
    success = collector_status == "ok" and source_freshness.get("status") != "blocked"
    last_valid_observation_utc = (
        _last_persisted_v2_observation(observations_path)
        if success
        else prior.get("last_valid_observation_utc")
    )
    return {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "runner_id": config.runner_id,
        "collector_run_id": collector_run_id,
        "started_at_utc": _iso(started_at),
        "finished_at_utc": _iso(finished_at),
        "last_successful_cycle_utc": (
            _iso(finished_at)
            if success
            else prior.get("last_successful_cycle_utc")
        ),
        "last_valid_observation_utc": last_valid_observation_utc,
        "status": "ok" if success else "blocked",
        "reason": "runtime_cycle_foundation_ok" if success else collector_reason,
        "sequence": prior_sequence + 1,
        "source_freshness": dict(source_freshness),
        "financial_config_sha256": financial_config_sha256,
        "observation_ledger_sha256": sha256_file(observations_path),
        "decision_ledger_sha256": sha256_file(decision_ledger_path),
        "closed_trades_sha256": sha256_file(closed_trades_path),
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }


def build_runtime_health(
    heartbeat: Mapping[str, Any], *, now_utc: datetime, max_age_seconds: float
) -> dict[str, Any]:
    blockers: list[str] = []
    if heartbeat.get("schema_version") != HEARTBEAT_SCHEMA_VERSION:
        blockers.append("heartbeat_schema_invalid")
    if heartbeat.get("status") != "ok":
        blockers.append(str(heartbeat.get("reason") or "heartbeat_status_not_ok"))
    try:
        finished = _parse_utc(heartbeat.get("finished_at_utc"), field="finished_at_utc")
        age = (now_utc.astimezone(UTC) - finished).total_seconds()
        if age < -60:
            blockers.append("heartbeat_finished_at_in_future")
        elif age > max_age_seconds:
            blockers.append("heartbeat_stale")
    except ValueError as exc:
        age = None
        blockers.append(str(exc))
    if heartbeat.get("collection_clock_started") is not False:
        blockers.append("collection_clock_started_must_be_false")
    if heartbeat.get("prospective_collection_running_proven") is not False:
        blockers.append("prospective_collection_running_proven_must_be_false")
    safety = heartbeat.get("safety_flags")
    if not isinstance(safety, Mapping):
        blockers.append("heartbeat_safety_flags_missing")
    else:
        for key, expected in RUNTIME_SAFETY_FLAGS.items():
            if safety.get(key) is not expected:
                blockers.append(f"heartbeat_safety_violation:{key}")
    blockers = list(dict.fromkeys(blockers))
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "status": "blocked" if blockers else "ok",
        "reason": blockers[0] if blockers else "runtime_foundation_health_ok",
        "blockers": blockers,
        "heartbeat_age_seconds": age,
        "heartbeat_sequence": heartbeat.get("sequence"),
        "runner_id": heartbeat.get("runner_id"),
        "collector_run_id": heartbeat.get("collector_run_id"),
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }


def build_deployment_foundation_report(config: RuntimeFoundationConfig) -> dict[str, Any]:
    deployment = dict(config.deployment_foundation)
    return {
        "schema_version": "aibot_parity_prospective_runtime_deployment_foundation_v1",
        "status": "ok",
        "reason": deployment.get("reason"),
        "selected_mechanism": deployment.get("selected_mechanism"),
        "scheduler_registration_performed": False,
        "docker_service_added": False,
        "recurring_runner_available": True,
        "recurring_collection_proven": False,
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }



def _safe_exception_reason(exc: Exception) -> str:
    first_line = str(exc).splitlines()[0].strip()
    return first_line[:300] if first_line else type(exc).__name__


def _build_failure_runtime_evidence(
    *,
    root: Path,
    config: RuntimeFoundationConfig,
    collector_run_id: str,
    started_at: datetime,
    finished_at: datetime,
    reason: str,
    error_type: str,
    observations_path: Path,
    decision_ledger_path: Path,
    closed_trades_path: Path,
    heartbeat_path: Path,
    health_path: Path,
    policy: AtomicWritePolicy,
    write_heartbeat: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and optionally persist fail-closed evidence for a failed cycle."""

    try:
        prior = _read_prior_heartbeat(heartbeat_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        prior = {}
    try:
        fingerprint = build_paper_financial_config_fingerprint(
            project_root=root,
            paper_config_path=config.paper_config_path,
            strategy_path=config.strategy_path,
            strategy_name=config.strategy_name,
        )
        financial_config_sha256: str | None = fingerprint.paper_financial_config_sha256
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        financial_config_sha256 = None

    source_freshness = {
        "status": "blocked",
        "reason": "runtime_cycle_exception",
        "error_type": error_type,
        "age_seconds": None,
        "max_age_seconds": config.max_snapshot_age_seconds,
    }
    heartbeat = {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "runner_id": config.runner_id,
        "collector_run_id": collector_run_id,
        "started_at_utc": _iso(started_at),
        "finished_at_utc": _iso(finished_at),
        "last_successful_cycle_utc": prior.get("last_successful_cycle_utc"),
        "last_valid_observation_utc": prior.get("last_valid_observation_utc"),
        "status": "blocked",
        "reason": reason,
        "error_type": error_type,
        "sequence": int(prior.get("sequence") or 0) + 1,
        "source_freshness": source_freshness,
        "financial_config_sha256": financial_config_sha256,
        "observation_ledger_sha256": sha256_file(observations_path),
        "decision_ledger_sha256": sha256_file(decision_ledger_path),
        "closed_trades_sha256": sha256_file(closed_trades_path),
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }
    health = build_runtime_health(
        heartbeat,
        now_utc=finished_at,
        max_age_seconds=max(config.max_snapshot_age_seconds, 60.0),
    )
    if write_heartbeat:
        atomic_write_json(heartbeat_path, heartbeat, policy=policy, allow_nan=False)
        atomic_write_json(health_path, health, policy=policy, allow_nan=False)
    return heartbeat, health


def _lock_contention_blocker(root: Path) -> str | None:
    """Return a blocker when the runtime-cycle lock is currently held elsewhere."""

    lock_path = _resolve(root, DEFAULT_LOCK)
    if not lock_path.exists():
        return None
    probe = _InterProcessFileLock(lock_path, timeout_seconds=0.0)
    try:
        probe.acquire()
    except AtomicWriteError:
        return "runtime_cycle_lock_unavailable"
    try:
        return None
    finally:
        probe.release()


def run_runtime_foundation_cycle(
    *,
    project_root: str | Path,
    aibot_snapshot_path: str | Path,
    decision_ledger_path: str | Path,
    closed_trades_path: str | Path,
    allow_paper_runtime_read: bool,
    write_evidence: bool = False,
    write_heartbeat: bool = False,
    runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG,
    preregistration_path: str | Path = DEFAULT_PREREGISTRATION,
    observations_path: str | Path = DEFAULT_OBSERVATIONS,
    assignments_path: str | Path = DEFAULT_ASSIGNMENTS,
    collector_report_path: str | Path = DEFAULT_COLLECTOR_REPORT,
    now_utc: datetime | None = None,
) -> RuntimeCycleResult:
    """Execute one restart-safe, lock-serialized prospective collection cycle."""

    root = Path(project_root).resolve()
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    started = now
    config = load_runtime_foundation_config(project_root=root, path=runtime_config_path)
    snapshot_path = _resolve(root, aibot_snapshot_path)
    ledger_path = _resolve(root, decision_ledger_path)
    closed_path = _resolve(root, closed_trades_path)
    observation_target = _resolve(root, observations_path)
    heartbeat_target = _resolve(root, config.heartbeat_path)
    health_target = _resolve(root, config.health_path)
    collector_run_id = f"collector-run-{uuid.uuid4().hex}"

    if not allow_paper_runtime_read:
        raise ValueError("paper_runtime_read_requires_explicit_allow")
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"aibot_snapshot_not_found:{snapshot_path}")
    if not ledger_path.is_file():
        raise FileNotFoundError(f"decision_ledger_not_found:{ledger_path}")
    if not closed_path.is_file():
        raise FileNotFoundError(f"closed_trades_not_found:{closed_path}")

    report_root = (root / "data" / "reports" / "aibot_parity").resolve()
    report_root.mkdir(parents=True, exist_ok=True)
    policy = AtomicWritePolicy.restricted(
        [report_root],
        working_directory=root,
        lock_timeout_seconds=config.lock_timeout_seconds,
    )
    cycle_lock = _InterProcessFileLock(
        _resolve(root, DEFAULT_LOCK), timeout_seconds=config.lock_timeout_seconds
    )

    collector_result: CollectionResult | None = None
    heartbeat: dict[str, Any] = {}
    health: dict[str, Any] = {}
    cycle_lock.acquire()
    try:
        fingerprint = build_paper_financial_config_fingerprint(
            project_root=root,
            paper_config_path=config.paper_config_path,
            strategy_path=config.strategy_path,
            strategy_name=config.strategy_name,
        )
        fingerprint_valid = (
            fingerprint.paper_financial_config_sha256
            == config.expected_financial_config_sha256
        )
        snapshots = load_aibot_snapshots(snapshot_path)
        freshness = _snapshot_freshness(
            snapshots,
            now_utc=now,
            max_age_seconds=config.max_snapshot_age_seconds,
        )
        preregistration = load_preregistration(_resolve(root, preregistration_path))
        existing = read_observation_ledger(observation_target)
        ledger = load_decision_ledger_jsonl(ledger_path)
        closed_trades, closed_diagnostics = load_normalized_closed_trades(
            project_root=root, source_path=closed_path
        )
        collector_result = collect_prospective_evidence(
            preregistration=preregistration,
            snapshots=snapshots,
            decisions=ledger.decisions,
            trade_links=ledger.trade_links,
            closed_trades=closed_trades,
            existing_observations=existing,
            financial_config_unchanged=fingerprint_valid,
            paper_financial_config_sha256=fingerprint.paper_financial_config_sha256,
            expected_financial_config_sha256=config.expected_financial_config_sha256,
            captured_at_utc=now,
            collector_run_id=collector_run_id,
        )
        report = dict(collector_result.report)
        runtime_blockers: list[str] = []
        if freshness.get("status") == "blocked":
            runtime_blockers.append(str(freshness.get("reason")))
        if not fingerprint_valid:
            runtime_blockers.append("FINANCIAL_CONFIG_FINGERPRINT_MISMATCH")
        if runtime_blockers:
            combined = list(
                dict.fromkeys([*report.get("collector_blockers", []), *runtime_blockers])
            )
            report["status"] = "blocked"
            report["reason"] = combined[0]
            report["collector_blockers"] = combined
            report["collector_blocker_count"] = len(combined)
        report["runtime_foundation"] = {
            "schema_version": SCHEMA_VERSION,
            "runner_id": config.runner_id,
            "collector_run_id": collector_run_id,
            "source_freshness": freshness,
            "financial_config_fingerprint": fingerprint.to_dict(),
            "financial_config_fingerprint_valid": fingerprint_valid,
            "closed_trade_diagnostics": closed_diagnostics,
            "recurring_runner_available": True,
            "runner_restart_safe": True,
            "runner_lock_safe": True,
            "runner_idempotent": True,
            "heartbeat_available": True,
            "healthcheck_available": True,
            "deployment_foundation": build_deployment_foundation_report(config),
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
        }

        if report["status"] == "ok" and write_evidence:
            appended = write_observations_idempotent(
                project_root=root,
                path=observation_target,
                observations=collector_result.observations,
            )
            report["observations_appended"] = appended
            assignment_target = resolve_assignments_path(root, assignments_path)
            assignments_appended = write_assignments_idempotent(
                root,
                assignment_target,
                immutable_assignment_rows(collector_result.assignments),
            )
            report["assignments_appended"] = assignments_appended
            report["write_observations_performed"] = appended > 0
            report["write_assignments_performed"] = assignments_appended > 0
            report["write_performed"] = bool(appended or assignments_appended)
        else:
            report["write_observations_performed"] = False
            report["write_assignments_performed"] = False
            report["write_performed"] = False

        report["write_report_performed"] = False

        prior = _read_prior_heartbeat(heartbeat_target)
        finished = datetime.now(UTC) if now_utc is None else now
        heartbeat = _build_heartbeat(
            config=config,
            collector_run_id=collector_run_id,
            started_at=started,
            finished_at=finished,
            collector_status=str(report["status"]),
            collector_reason=str(report["reason"]),
            prior=prior,
            source_freshness=freshness,
            financial_config_sha256=fingerprint.paper_financial_config_sha256,
            observations_path=observation_target,
            decision_ledger_path=ledger_path,
            closed_trades_path=closed_path,
        )
        health = build_runtime_health(
            heartbeat,
            now_utc=finished,
            max_age_seconds=max(config.max_snapshot_age_seconds, 60.0),
        )
        if write_heartbeat:
            atomic_write_json(heartbeat_target, heartbeat, policy=policy, allow_nan=False)
            atomic_write_json(health_target, health, policy=policy, allow_nan=False)

        report["heartbeat"] = heartbeat
        report["health"] = health
        report["heartbeat_write_performed"] = bool(write_heartbeat)
        report["health_write_performed"] = bool(write_heartbeat)
        report["collection_clock_started"] = False
        report["prospective_collection_running_proven"] = False
        report["recurring_collection_proven"] = False
        report["paper_treatment_release_allowed"] = False
        if write_evidence:
            collector_target = resolve_report_path(root, collector_report_path)
            report["write_report_performed"] = True
            report["write_performed"] = True
            write_report(root, collector_target, report)
        return RuntimeCycleResult(report, collector_result, heartbeat, health)
    except (OSError, ValueError, TypeError, json.JSONDecodeError, AtomicWriteError) as exc:
        finished = datetime.now(UTC) if now_utc is None else now
        reason = _safe_exception_reason(exc)
        heartbeat, health = _build_failure_runtime_evidence(
            root=root,
            config=config,
            collector_run_id=collector_run_id,
            started_at=started,
            finished_at=finished,
            reason=reason,
            error_type=type(exc).__name__,
            observations_path=observation_target,
            decision_ledger_path=ledger_path,
            closed_trades_path=closed_path,
            heartbeat_path=heartbeat_target,
            health_path=health_target,
            policy=policy,
            write_heartbeat=write_heartbeat,
        )
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "blocked",
            "reason": reason,
            "error_type": type(exc).__name__,
            "runner_id": config.runner_id,
            "collector_run_id": collector_run_id,
            "recurring_runner_available": True,
            "runner_restart_safe": True,
            "runner_lock_safe": True,
            "runner_idempotent": True,
            "heartbeat_available": True,
            "healthcheck_available": True,
            "heartbeat": heartbeat,
            "health": health,
            "heartbeat_write_performed": bool(write_heartbeat),
            "health_write_performed": bool(write_heartbeat),
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
            "recurring_collection_proven": False,
            "paper_treatment_release_allowed": False,
            "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
            **RUNTIME_SAFETY_FLAGS,
        }
        return RuntimeCycleResult(report, collector_result, heartbeat, health)
    finally:
        cycle_lock.release()


def check_runtime_foundation_health(
    *,
    project_root: str | Path,
    runtime_config_path: str | Path = DEFAULT_RUNTIME_CONFIG,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = load_runtime_foundation_config(project_root=root, path=runtime_config_path)
    heartbeat_path = _resolve(root, config.heartbeat_path)
    if not heartbeat_path.is_file():
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "status": "blocked",
            "reason": "heartbeat_missing",
            "blockers": ["heartbeat_missing"],
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
            "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
            **RUNTIME_SAFETY_FLAGS,
        }
    heartbeat = _read_prior_heartbeat(heartbeat_path)
    health = build_runtime_health(
        heartbeat,
        now_utc=(now_utc or datetime.now(UTC)).astimezone(UTC),
        max_age_seconds=max(config.max_snapshot_age_seconds, 60.0),
    )
    lock_blocker = _lock_contention_blocker(root)
    if lock_blocker is not None:
        blockers = list(dict.fromkeys([*health.get("blockers", []), lock_blocker]))
        health["status"] = "blocked"
        health["reason"] = blockers[0]
        health["blockers"] = blockers
    return health


__all__ = [
    "DEFAULT_HEALTH",
    "DEFAULT_HEARTBEAT",
    "DEFAULT_RUNTIME_CONFIG",
    "HEALTH_SCHEMA_VERSION",
    "HEARTBEAT_SCHEMA_VERSION",
    "RUNTIME_SAFETY_FLAGS",
    "RuntimeCycleResult",
    "RuntimeFoundationConfig",
    "SCHEMA_VERSION",
    "build_deployment_foundation_report",
    "build_runtime_health",
    "check_runtime_foundation_health",
    "load_runtime_foundation_config",
    "run_runtime_foundation_cycle",
    "sha256_file",
]
