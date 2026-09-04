from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.audit_aibot_parity_prospective_runtime_activation_foundation_v1 as audit_cli
import scripts.run_aibot_parity_paper_ab_prospective_collector_v1 as collector_cli
import scripts.run_aibot_parity_prospective_runtime_cycle_v1 as runtime_cycle_cli
from smartcrypto.execution.decision_ledger_v4_2.contracts import seal_decision_record
from smartcrypto.research.aibot_parity_orchestrator.contracts import (
    REQUIRED_SOURCE_NAMES,
    AibotParityPipelineSnapshot,
    PipelineSourceView,
    PipelineStatus,
    PointInTimeStatus,
)
from smartcrypto.research.aibot_parity_paper_ab_prospective_collector import (
    LEGACY_OBSERVATION_SCHEMA_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    CollectionResult,
    build_paper_financial_config_fingerprint,
)
from smartcrypto.research.aibot_parity_paper_ab_prospective_collector.runtime_foundation import (
    HEARTBEAT_SCHEMA_VERSION,
    RUNTIME_SAFETY_FLAGS,
    build_deployment_foundation_report,
    build_runtime_health,
    check_runtime_foundation_health,
    load_runtime_foundation_config,
    run_runtime_foundation_cycle,
)
from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import _InterProcessFileLock

ROOT = Path(__file__).resolve().parents[1]
PAPER_CONFIG = ROOT / "freqtrade/user_data/config.paper.json"
STRATEGY = ROOT / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"




@pytest.mark.parametrize(
    "script_name",
    [
        "audit_aibot_parity_prospective_runtime_activation_foundation_v1.py",
        "check_aibot_parity_prospective_runtime_health_v1.py",
        "run_aibot_parity_paper_ab_prospective_collector_v1.py",
        "run_aibot_parity_prospective_runtime_cycle_v1.py",
    ],
)
def test_direct_cli_bootstraps_current_worktree_imports(
    tmp_path: Path, script_name: str
) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

def _copy_financial_sources(tmp_path: Path) -> tuple[Path, Path]:
    config = tmp_path / "freqtrade/user_data/config.paper.json"
    strategy = tmp_path / "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
    config.parent.mkdir(parents=True, exist_ok=True)
    strategy.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(PAPER_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    strategy.write_text(STRATEGY.read_text(encoding="utf-8"), encoding="utf-8")
    return config, strategy


def _fingerprint(tmp_path: Path) -> str:
    return build_paper_financial_config_fingerprint(
        project_root=tmp_path
    ).paper_financial_config_sha256


def test_financial_fingerprint_matches_preregistered_runtime_foundation() -> None:
    config = load_runtime_foundation_config(project_root=ROOT)
    fingerprint = build_paper_financial_config_fingerprint(project_root=ROOT)
    assert fingerprint.paper_financial_config_sha256 == config.expected_financial_config_sha256
    assert fingerprint.to_dict()["secrets_projected"] is False


def test_financial_fingerprint_is_deterministic(tmp_path: Path) -> None:
    _copy_financial_sources(tmp_path)
    assert _fingerprint(tmp_path) == _fingerprint(tmp_path)


def test_financial_fingerprint_uses_source_stable_leverage_semantics() -> None:
    fingerprint = build_paper_financial_config_fingerprint(project_root=ROOT)
    strategy_payload = fingerprint.canonical_payload["strategy"]
    assert "leverage_source_sha256_lf" in strategy_payload
    assert "leverage_semantics_ast" not in strategy_payload
    assert len(strategy_payload["leverage_source_sha256_lf"]) == 64


@pytest.mark.parametrize(
    ("field", "mutator"),
    [
        ("minimal_roi", lambda payload: payload.__setitem__("minimal_roi", {"0": 0.03})),
        ("stoploss", lambda payload: payload.__setitem__("stoploss", -0.02)),
        (
            "pair_whitelist",
            lambda payload: payload["exchange"].__setitem__(
                "pair_whitelist", ["BTC/USDT:USDT"]
            ),
        ),
        ("stake_amount", lambda payload: payload.__setitem__("stake_amount", 60)),
    ],
)
def test_financial_fingerprint_changes_for_economic_config_mutations(
    tmp_path: Path, field: str, mutator
) -> None:
    config, _ = _copy_financial_sources(tmp_path)
    baseline = _fingerprint(tmp_path)
    payload = json.loads(config.read_text(encoding="utf-8"))
    mutator(payload)
    config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert _fingerprint(tmp_path) != baseline, field


def test_financial_fingerprint_changes_when_leverage_semantics_change(tmp_path: Path) -> None:
    _, strategy = _copy_financial_sources(tmp_path)
    baseline = _fingerprint(tmp_path)
    source = strategy.read_text(encoding="utf-8")
    assert "return min(2.0, max_leverage)" in source
    strategy.write_text(
        source.replace("return min(2.0, max_leverage)", "return min(3.0, max_leverage)"),
        encoding="utf-8",
    )
    assert _fingerprint(tmp_path) != baseline




def test_financial_fingerprint_tracks_whole_strategy_behavior(tmp_path: Path) -> None:
    _, strategy = _copy_financial_sources(tmp_path)
    baseline = _fingerprint(tmp_path)
    source = strategy.read_text(encoding="utf-8")
    target = 'dataframe.at[last_index, "enter_long"] = 1'
    assert target in source
    strategy.write_text(
        source.replace(target, 'dataframe.at[last_index, "enter_long"] = 0'),
        encoding="utf-8",
    )
    assert _fingerprint(tmp_path) != baseline


def test_financial_fingerprint_normalizes_strategy_line_endings(tmp_path: Path) -> None:
    _, strategy = _copy_financial_sources(tmp_path)
    baseline = _fingerprint(tmp_path)
    source = strategy.read_text(encoding="utf-8")
    strategy.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
    assert _fingerprint(tmp_path) == baseline


def test_financial_fingerprint_ignores_secret_only_mutations(tmp_path: Path) -> None:
    config, _ = _copy_financial_sources(tmp_path)
    baseline = _fingerprint(tmp_path)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["exchange"]["key"] = "do-not-project-this-key"
    payload["exchange"]["secret"] = "do-not-project-this-secret"
    payload["telegram"]["token"] = "do-not-project-this-token"
    payload["api_server"]["password"] = "do-not-project-this-password"
    config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    assert _fingerprint(tmp_path) == baseline


def test_runtime_foundation_config_is_non_operational_and_scheduler_not_registered() -> None:
    config = load_runtime_foundation_config(project_root=ROOT)
    deployment = build_deployment_foundation_report(config)
    assert deployment["status"] == "ok"
    assert deployment["recurring_runner_available"] is True
    assert deployment["recurring_collection_proven"] is False
    assert deployment["scheduler_registration_performed"] is False
    assert deployment["collection_clock_started"] is False
    assert deployment["prospective_collection_running_proven"] is False


def _heartbeat(now: datetime) -> dict[str, object]:
    return {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "runner_id": "runner-test",
        "collector_run_id": "collector-run-test",
        "started_at_utc": (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        "finished_at_utc": now.isoformat().replace("+00:00", "Z"),
        "last_successful_cycle_utc": now.isoformat().replace("+00:00", "Z"),
        "last_valid_observation_utc": now.isoformat().replace("+00:00", "Z"),
        "status": "ok",
        "reason": "runtime_cycle_foundation_ok",
        "sequence": 7,
        "source_freshness": {"status": "ok"},
        "financial_config_sha256": "f" * 64,
        "observation_ledger_sha256": None,
        "decision_ledger_sha256": "d" * 64,
        "closed_trades_sha256": "c" * 64,
        "collection_clock_started": False,
        "prospective_collection_running_proven": False,
        "safety_flags": dict(RUNTIME_SAFETY_FLAGS),
        **RUNTIME_SAFETY_FLAGS,
    }


def test_runtime_health_accepts_fresh_safe_heartbeat_and_blocks_stale() -> None:
    now = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
    fresh = build_runtime_health(_heartbeat(now), now_utc=now, max_age_seconds=60)
    assert fresh["status"] == "ok"
    assert fresh["collection_clock_started"] is False
    assert fresh["prospective_collection_running_proven"] is False

    stale_heartbeat = _heartbeat(now - timedelta(minutes=10))
    stale = build_runtime_health(stale_heartbeat, now_utc=now, max_age_seconds=60)
    assert stale["status"] == "blocked"
    assert "heartbeat_stale" in stale["blockers"]


def test_healthcheck_missing_heartbeat_is_fail_closed(tmp_path: Path) -> None:
    config_dir = tmp_path / "config/research"
    config_dir.mkdir(parents=True)
    payload = json.loads(
        (ROOT / "config/research/aibot_parity_prospective_runtime_foundation_v1.json").read_text(
            encoding="utf-8"
        )
    )
    (config_dir / "aibot_parity_prospective_runtime_foundation_v1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    report = check_runtime_foundation_health(project_root=tmp_path)
    assert report["status"] == "blocked"
    assert report["reason"] == "heartbeat_missing"
    assert report["collection_clock_started"] is False



def test_collector_cli_blocked_integrity_never_writes_observations_or_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = collector_cli._parser().parse_args(
        [
            "--project-root",
            str(ROOT),
            "--write-observations",
            "--write-assignments",
        ]
    )
    blocked = CollectionResult(
        report={
            "status": "blocked",
            "reason": "AIBOT_SNAPSHOT_POINT_IN_TIME_NOT_VALID:test",
            "collection_clock_started": False,
            "prospective_collection_running_proven": False,
            "write_performed": False,
            "observations_appended": 0,
            "assignments_appended": 0,
        },
        observations=[{"should_not": "write"}],
        candidate_rows=[],
        assignments=[{"should_not": "write"}],
    )
    monkeypatch.setattr(
        collector_cli,
        "collect_prospective_evidence",
        lambda **_kwargs: blocked,
    )

    def forbidden_write(*_args, **_kwargs):
        raise AssertionError("blocked collector must not persist evidence rows")

    monkeypatch.setattr(collector_cli, "write_observations_idempotent", forbidden_write)
    monkeypatch.setattr(collector_cli, "write_assignments_idempotent", forbidden_write)
    report = collector_cli.run(args)
    assert report["status"] == "blocked"
    assert report["write_observations_performed"] is False
    assert report["write_assignments_performed"] is False
    assert report["write_performed"] is False


def test_collector_cli_exit_code_is_semantic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["collector", "--json"])
    monkeypatch.setattr(collector_cli, "run", lambda args: {"status": "ok", "reason": "ok"})
    assert collector_cli.main() == 0

    monkeypatch.setattr(
        collector_cli,
        "run",
        lambda args: {"status": "blocked", "reason": "blocked_for_test"},
    )
    assert collector_cli.main() == 2


def test_collector_cli_file_error_is_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["collector", "--json"])

    def fail(_args):
        raise FileNotFoundError("missing-for-test")

    monkeypatch.setattr(collector_cli, "run", fail)
    assert collector_cli.main() == 2


def test_foundation_audit_is_green_and_never_starts_clock() -> None:
    report = audit_cli.build_audit(ROOT)
    assert report["status"] == "ok"
    assert report["checks"]["COLLECTOR_PIT_FAIL_CLOSED"] is True
    assert report["checks"]["FINANCIAL_CONFIG_FINGERPRINT_VALID"] is True
    assert report["checks"]["RECURRING_RUNNER_AVAILABLE"] is True
    assert report["checks"]["HEARTBEAT_AVAILABLE"] is True
    assert report["checks"]["HEALTHCHECK_AVAILABLE"] is True
    assert report["collection_clock_started"] is False
    assert report["prospective_collection_running_proven"] is False
    assert report["paper_treatment_release_allowed"] is False


def _copy_runtime_foundation_prerequisites(tmp_path: Path) -> None:
    _copy_financial_sources(tmp_path)
    runtime_config_source = (
        ROOT / "config/research/aibot_parity_prospective_runtime_foundation_v1.json"
    )
    prereg_source = ROOT / "config/research/aibot_parity_paper_ab_soak_v1.json"
    target_dir = tmp_path / "config/research"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / runtime_config_source.name).write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target_dir / prereg_source.name).write_text(
        prereg_source.read_text(encoding="utf-8"), encoding="utf-8"
    )


def test_runtime_cycle_exception_writes_fail_closed_heartbeat(tmp_path: Path) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    snapshot = tmp_path / "snapshot.json"
    ledger = tmp_path / "ledger.jsonl"
    closed = tmp_path / "closed.json"
    snapshot.write_text("{broken-json", encoding="utf-8")
    ledger.write_text("{}\n", encoding="utf-8")
    closed.write_text("[]\n", encoding="utf-8")

    result = run_runtime_foundation_cycle(
        project_root=tmp_path,
        aibot_snapshot_path=snapshot,
        decision_ledger_path=ledger,
        closed_trades_path=closed,
        allow_paper_runtime_read=True,
        write_evidence=False,
        write_heartbeat=True,
        now_utc=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
    )

    assert result.report["status"] == "blocked"
    assert result.report["collection_clock_started"] is False
    assert result.report["prospective_collection_running_proven"] is False
    assert result.heartbeat["status"] == "blocked"
    assert result.health["status"] == "blocked"
    heartbeat_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_prospective_runtime_heartbeat_v1.json"
    )
    health_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_prospective_runtime_health_v1.json"
    )
    assert heartbeat_path.is_file()
    assert health_path.is_file()



def _write_valid_runtime_sources(tmp_path: Path, *, now: datetime) -> tuple[Path, Path, Path]:
    snapshot_path = tmp_path / "snapshot.json"
    ledger_path = tmp_path / "ledger.jsonl"
    closed_path = tmp_path / "closed.json"

    decision = seal_decision_record(
        {
            "event_id": "decision-runtime-1",
            "signal_id": "signal-runtime-1",
            "candidate_id": "candidate-runtime-1",
            "correlation_id": "corr-runtime-1",
            "idempotency_key": "idem-runtime-1",
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": "long",
            "feature_timestamp": now - timedelta(minutes=1),
            "decision_timestamp": now - timedelta(seconds=30),
            "feature_contract_version": "features-v1",
            "feature_hash": "1" * 64,
            "model_id": "research-model",
            "model_version": "v1",
            "model_hash": "2" * 64,
            "qlib_score": 0.2,
            "calibrated_probability": 0.6,
            "expected_net_pnl": 1.0,
            "fast_stop_probability": 0.1,
            "regime": "trend",
            "alignment": "aligned",
            "ai_shadow_decision": "ALLOW",
            "ai_shadow_reasons": [],
            "risk_decision": "APPROVED",
            "risk_reasons": [],
            "approved_stake_usdt": 10.0,
            "approved_leverage": 1.0,
            "final_decision": "ALLOW",
            "final_reasons": ["paper_baseline"],
        }
    )
    views = tuple(
        PipelineSourceView(
            source_name=name,
            status="OK",
            point_in_time_status=PointInTimeStatus.VALID,
            source_hash=str(index + 1) * 64,
            evidence_time_utc=now - timedelta(seconds=1),
            reason="point_in_time_valid",
        )
        for index, name in enumerate(REQUIRED_SOURCE_NAMES)
    )
    snapshot = AibotParityPipelineSnapshot(
        cycle_id="runtime-cycle-1",
        request_id="runtime-request-1",
        decision_time_utc=now,
        created_at_utc=now,
        status=PipelineStatus.READY_SHADOW,
        reason="counterfactual_signal_ready_after_shadow_riskmanager_allow",
        final_action="WOULD_SIGNAL",
        would_signal=True,
        qlib_status="BLOCKED_EXTERNAL",
        qlib_blocked_external=True,
        ensemble_action="ACCEPT",
        riskmanager_shadow_decision="ALLOW",
        selected_candidate_ids=(decision.candidate_id,),
        required_sources_present=tuple(REQUIRED_SOURCE_NAMES),
        missing_required_sources=(),
        blocking_reasons=(),
        source_views=views,
    )
    snapshot_path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), sort_keys=True), encoding="utf-8"
    )
    ledger_path.write_text(
        json.dumps(decision.model_dump(mode="json"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    closed_path.write_text(
        json.dumps(
            [
                {
                    "trade_id": "999",
                    "symbol": "ETHUSDT",
                    "side": "long",
                    "open_time": "2026-09-04T10:00:00Z",
                    "close_time": "2026-09-04T11:00:00Z",
                    "entry_price": 100.0,
                    "exit_price": 101.0,
                    "pnl": 1.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    return snapshot_path, ledger_path, closed_path


def _seal_observation(payload: dict[str, object]) -> dict[str, object]:
    rendered = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return {**payload, "observation_sha256": hashlib.sha256(rendered).hexdigest()}


def test_legacy_v1_ledger_never_advances_last_valid_observation_utc(
    tmp_path: Path,
) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    snapshot, ledger, closed = _write_valid_runtime_sources(tmp_path, now=now)
    observation_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_observations_v1.jsonl"
    )
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = _seal_observation(
        {
            "schema_version": LEGACY_OBSERVATION_SCHEMA_VERSION,
            "observation_id": "obs-"
            + hashlib.sha256(
                b"runtime-cycle-1|candidate-runtime-1"
            ).hexdigest(),
            "candidate_id": "candidate-runtime-1",
            "cycle_id": "runtime-cycle-1",
            "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
            "treatment_action": "ACCEPT",
            "riskmanager_shadow_decision": "ALLOW",
            "decision_payload_sha256": "1" * 64,
            "aibot_snapshot_sha256": "2" * 64,
        }
    )
    observation_path.write_text(
        json.dumps(legacy, sort_keys=True) + "\n", encoding="utf-8"
    )

    result = run_runtime_foundation_cycle(
        project_root=tmp_path,
        aibot_snapshot_path=snapshot,
        decision_ledger_path=ledger,
        closed_trades_path=closed,
        allow_paper_runtime_read=True,
        write_evidence=True,
        write_heartbeat=True,
        now_utc=now + timedelta(seconds=1),
    )

    assert result.report["status"] == "ok"
    assert result.heartbeat["last_valid_observation_utc"] is None
    persisted = observation_path.read_text(encoding="utf-8").splitlines()
    assert len(persisted) == 1
    assert json.loads(persisted[0])["schema_version"] == LEGACY_OBSERVATION_SCHEMA_VERSION


def test_blocked_cycle_does_not_advance_from_unpersisted_v2_observation(
    tmp_path: Path,
) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    snapshot, ledger, closed = _write_valid_runtime_sources(tmp_path, now=now)
    config = load_runtime_foundation_config(project_root=tmp_path)
    heartbeat_path = tmp_path / config.heartbeat_path
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    prior_time = now - timedelta(hours=1)
    prior = _heartbeat(prior_time)
    prior["last_valid_observation_utc"] = prior_time.isoformat().replace(
        "+00:00", "Z"
    )
    heartbeat_path.write_text(json.dumps(prior), encoding="utf-8")

    result = run_runtime_foundation_cycle(
        project_root=tmp_path,
        aibot_snapshot_path=snapshot,
        decision_ledger_path=ledger,
        closed_trades_path=closed,
        allow_paper_runtime_read=True,
        write_evidence=True,
        write_heartbeat=True,
        now_utc=now + timedelta(hours=1),
    )

    assert result.report["status"] == "blocked"
    assert result.collector_result is not None
    assert any(
        row.get("schema_version") == OBSERVATION_SCHEMA_VERSION
        for row in result.collector_result.observations
    )
    observation_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_observations_v1.jsonl"
    )
    assert not observation_path.exists()
    assert result.heartbeat["last_valid_observation_utc"] == prior[
        "last_valid_observation_utc"
    ]


def test_runtime_cycle_is_restart_safe_and_idempotent(tmp_path: Path) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    snapshot, ledger, closed = _write_valid_runtime_sources(tmp_path, now=now)

    first = run_runtime_foundation_cycle(
        project_root=tmp_path,
        aibot_snapshot_path=snapshot,
        decision_ledger_path=ledger,
        closed_trades_path=closed,
        allow_paper_runtime_read=True,
        write_evidence=True,
        write_heartbeat=True,
        now_utc=now + timedelta(seconds=1),
    )
    assert first.report["status"] == "ok"
    assert first.report["observations_appended"] == 1
    assert first.report["assignments_appended"] == 1
    assert first.heartbeat["sequence"] == 1
    assert first.heartbeat["last_valid_observation_utc"] == (
        now + timedelta(seconds=1)
    ).isoformat().replace("+00:00", "Z")
    assert first.health["status"] == "ok"
    assert first.report["collection_clock_started"] is False
    assert first.report["prospective_collection_running_proven"] is False

    second = run_runtime_foundation_cycle(
        project_root=tmp_path,
        aibot_snapshot_path=snapshot,
        decision_ledger_path=ledger,
        closed_trades_path=closed,
        allow_paper_runtime_read=True,
        write_evidence=True,
        write_heartbeat=True,
        now_utc=now + timedelta(seconds=2),
    )
    assert second.report["status"] == "ok"
    assert second.report["observations_appended"] == 0
    assert second.report["assignments_appended"] == 0
    assert second.heartbeat["sequence"] == 2
    assert second.health["status"] == "ok"

    observation_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_paper_ab_prospective_observations_v1.jsonl"
    )
    assignment_path = (
        tmp_path
        / "data/reports/aibot_parity/aibot_parity_paper_ab_soak_assignments_v1.jsonl"
    )
    assert len(observation_path.read_text(encoding="utf-8").splitlines()) == 1
    assert len(assignment_path.read_text(encoding="utf-8").splitlines()) == 1


def test_healthcheck_detects_runtime_cycle_lock_contention(tmp_path: Path) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    config = load_runtime_foundation_config(project_root=tmp_path)
    heartbeat_path = tmp_path / config.heartbeat_path
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    heartbeat_path.write_text(json.dumps(_heartbeat(now)), encoding="utf-8")

    lock_path = (
        tmp_path
        / "data/reports/aibot_parity/.aibot_parity_prospective_runtime_foundation.lock"
    )
    lock = _InterProcessFileLock(lock_path, timeout_seconds=1.0)
    lock.acquire()
    try:
        health = check_runtime_foundation_health(
            project_root=tmp_path,
            now_utc=now,
        )
    finally:
        lock.release()
    assert health["status"] == "blocked"
    assert "runtime_cycle_lock_unavailable" in health["blockers"]


def test_runtime_foundation_config_rejects_heartbeat_path_outside_reports(
    tmp_path: Path,
) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    config_path = (
        tmp_path / "config/research/aibot_parity_prospective_runtime_foundation_v1.json"
    )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["heartbeat_path"] = "../outside-heartbeat.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="heartbeat_path_outside_reports"):
        load_runtime_foundation_config(project_root=tmp_path)


def test_runtime_foundation_config_rejects_unsafe_scheduler_registration(
    tmp_path: Path,
) -> None:
    _copy_runtime_foundation_prerequisites(tmp_path)
    config_path = (
        tmp_path / "config/research/aibot_parity_prospective_runtime_foundation_v1.json"
    )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["deployment_foundation"]["scheduler_registration_performed"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="scheduler_registration_performed_must_be_false"):
        load_runtime_foundation_config(project_root=tmp_path)


def test_runtime_cycle_cli_exit_code_is_semantic(monkeypatch: pytest.MonkeyPatch) -> None:
    class Result:
        def __init__(self, report: dict[str, object]) -> None:
            self.report = report

    argv = [
        "runtime-cycle",
        "--aibot-snapshot-json",
        "snapshot.json",
        "--decision-ledger-jsonl",
        "ledger.jsonl",
        "--closed-trades-path",
        "closed.csv",
        "--json",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        runtime_cycle_cli,
        "run_runtime_foundation_cycle",
        lambda **_kwargs: Result(
            {
                "status": "ok",
                "reason": "ok",
                "collection_clock_started": False,
                "health": {"status": "ok"},
            }
        ),
    )
    assert runtime_cycle_cli.main() == 0

    monkeypatch.setattr(
        runtime_cycle_cli,
        "run_runtime_foundation_cycle",
        lambda **_kwargs: Result(
            {
                "status": "blocked",
                "reason": "blocked_for_test",
                "collection_clock_started": False,
                "health": {"status": "blocked"},
            }
        ),
    )
    assert runtime_cycle_cli.main() == 2
