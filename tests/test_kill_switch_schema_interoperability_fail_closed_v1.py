from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

from smartcrypto.execution.order_manager import OrderManager
from smartcrypto.ops.execution_decision_l1 import collector, deployment_preflight, transport
from smartcrypto.ops.execution_decision_l1 import kill_switch_authority as authority
from smartcrypto.ops.execution_decision_l1.contracts import CollectorConfig, Symbol
from smartcrypto.risk import kill_switch_guard as canonical
from smartcrypto.risk import risk_manager as risk

T0 = "2026-07-01T00:00:00+00:00"


def entry(enabled: bool = False, **changes: object) -> dict[str, object]:
    return {
        "enabled": enabled,
        "reason": "fixture_explicit",
        "actor": "pytest",
        "updated_at": T0,
        **changes,
    }


def structured(**changes: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime_mode": "paper",
        "updated_at": T0,
        "global": entry(),
        "symbols": {},
        **changes,
    }


CASES = [
    ("legacy_active", {"enabled": True, "runtime_mode": "paper"}, canonical.GLOBAL_BLOCKED),
    ("legacy_clear", {"enabled": False, "runtime_mode": "paper"}, canonical.CORRUPTED),
    ("global_blocked", structured(**{"global": entry(True)}), canonical.GLOBAL_BLOCKED),
    ("symbol_blocked", structured(symbols={"ETHUSDT": entry(True)}), canonical.SYMBOL_BLOCKED),
    ("explicit_clear", structured(), canonical.CLEAR),
    (
        "contradictory_flat_clear",
        structured(enabled=False, **{"global": entry(True)}),
        canonical.CORRUPTED,
    ),
    ("contradictory_flat_active", structured(enabled=True), canonical.CORRUPTED),
    ("matching_dual_authorities", structured(enabled=False), canonical.CORRUPTED),
    ("missing", None, canonical.CORRUPTED),
    ("corrupt", b"{invalid", canonical.CORRUPTED),
    ("partial_json", b'{"schema_version":1,"global":', canonical.CORRUPTED),
    ("null", b"null", canonical.CORRUPTED),
    (
        "duplicate_keys",
        b'{"enabled":false,"enabled":true,"runtime_mode":"paper"}',
        canonical.CORRUPTED,
    ),
    ("nan", b'{"enabled":NaN,"runtime_mode":"paper"}', canonical.CORRUPTED),
    (
        "partial_migration",
        {"enabled": True, "runtime_mode": "paper", "symbols": {}},
        canonical.CORRUPTED,
    ),
    ("missing_entry_metadata", structured(**{"global": {"enabled": False}}), canonical.CORRUPTED),
    ("missing_parent_clock", structured(updated_at=None), canonical.CORRUPTED),
    ("invalid_clock", structured(updated_at="not-a-clock"), canonical.CORRUPTED),
    ("naive_clock", structured(updated_at="2026-07-01T00:00:00"), canonical.CORRUPTED),
    ("future_clock", structured(updated_at="2999-01-01T00:00:00Z"), canonical.CORRUPTED),
    ("older_parent_clock", structured(updated_at="2026-06-01T00:00:00Z"), canonical.CORRUPTED),
    ("invalid_actor", structured(**{"global": entry(actor=3)}), canonical.CORRUPTED),
    (
        "contradictory_actor",
        structured(**{"global": entry(operator="another-actor")}),
        canonical.CORRUPTED,
    ),
    (
        "contradictory_clock",
        structured(**{"global": entry(timestamp_utc="2026-06-01T00:00:00Z")}),
        canonical.CORRUPTED,
    ),
    ("invalid_symbol", structured(symbols={"ETH USDT": entry()}), canonical.CORRUPTED),
    ("lowercase_identity", structured(symbols={"ethusdt": entry()}), canonical.CORRUPTED),
    (
        "identity_collision",
        structured(symbols={"ethusdt": entry(), "ETHUSDT": entry()}),
        canonical.CORRUPTED,
    ),
    ("wrong_schema", structured(schema_version=True), canonical.CORRUPTED),
    ("wrong_mode", structured(runtime_mode="live"), canonical.CORRUPTED),
    ("default_clear", canonical.default_state(), canonical.CORRUPTED),
]


def fixture_runtime(tmp_path: Path, value: object) -> tuple[Path, Path]:
    root = tmp_path / "paper"
    ledger = root / authority.LEDGER_RELATIVE
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"")
    path = root / canonical.DEFAULT_KILL_SWITCH_PATH
    if value is not None:
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    return root, path


def files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture(autouse=True)
def isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(authority, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(authority, "READ_DEADLINE_SECONDS", 0.15)
    monkeypatch.setattr(
        deployment_preflight,
        "local_clock_diagnostic",
        lambda: {
            "status": "UNPROVEN",
            "reason": "fixture_no_clock_probe",
            "network_executed": False,
        },
    )


@pytest.mark.parametrize("name,value,status", CASES, ids=[row[0] for row in CASES])
def test_cross_consumer_read_only_matrix(
    tmp_path: Path, name: str, value: object, status: str
) -> None:
    root, path = fixture_runtime(tmp_path, value)
    before = files(root)
    guard = canonical.KillSwitchGuard(state_path=path, event_log_path=root / "audit-events.jsonl")
    result = guard.inspect("ETHUSDT")
    projection = risk.load_kill_switch(path, symbol="ETHUSDT")
    assert result.status == status and projection["status"] == status
    assert result.block_operation == projection["enabled"] == (status != canonical.CLEAR)
    if status == canonical.CORRUPTED:
        with pytest.raises(authority.AuthorityDenied):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("BTCUSDT", "ETHUSDT"))
    else:
        snapshot = authority.read_authority(
            root, root / authority.LEDGER_RELATIVE, ("BTCUSDT", "ETHUSDT")
        )
        assert snapshot.status == status
        if name == "legacy_active":
            assert snapshot.source_format == "legacy" and snapshot.updated_at_utc is None
            assert result.reason is None and result.actor is None
    assert files(root) == before


@pytest.mark.parametrize("name,value,status", CASES, ids=[row[0] for row in CASES])
def test_risk_evaluation_uses_shared_authority_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: object, status: str
) -> None:
    root, path = fixture_runtime(tmp_path, value)
    config = {
        "runtime_mode": "paper",
        "paths": {"kill_switch": str(path), "primary_signals": str(root / "missing-signals.json")},
        "limits": {"require_non_empty_signals": False},
        "safety": {},
    }
    monkeypatch.setattr(risk, "load_yaml", lambda _: config)
    monkeypatch.setattr(risk, "inspect_prediction_age", lambda *_: {"stale": False})
    monkeypatch.setattr(
        risk, "inspect_freqtrade_sqlite", lambda _: {"exists": True, "open_rows": 0}
    )
    before = files(root)
    decision = risk.evaluate_risk(root / "fixture-config.yml", write_report=False)
    assert decision.approved == (status == canonical.CLEAR)
    assert decision.metrics["kill_switch"]["status"] == status
    if status != canonical.CLEAR:
        assert any(reason.startswith("kill_switch_enabled:") for reason in decision.reasons)
    assert files(root) == before


class NoNetwork:
    def fetch(self, symbol: Symbol, timeout: float) -> transport.ReceivedTicker:
        pytest.fail("blocked state reached a public request")


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": True, "runtime_mode": "paper"},
        {"enabled": False, "runtime_mode": "paper"},
        structured(enabled=False, **{"global": entry(True)}),
    ],
)
def test_blocked_legacy_and_mixed_startup_never_reach_network(
    tmp_path: Path, value: object
) -> None:
    root, _ = fixture_runtime(tmp_path, value)
    before = files(root)
    session = collector.Collector(
        root / authority.LEDGER_RELATIVE, CollectorConfig(), runtime_root=root, client=NoNetwork()
    )
    report = session.run(0.05)
    assert report["status"] == "blocked"
    assert not report["network_calls_executed"] and not report["write_performed"]
    assert report["COLLECTOR_KILLSWITCH_ENFORCEMENT"] == "BLOCKED"
    assert files(root) == before


def test_real_equivalent_legacy_active_preflight_retains_global_block_proof(tmp_path: Path) -> None:
    root, _ = fixture_runtime(tmp_path, {"enabled": True, "runtime_mode": "paper"})
    before = files(root)
    metadata, check = deployment_preflight.inspect_paper_kill_switch(root)
    assert check.status == "FAILED" and check.reason == "authority_global_blocked"
    assert metadata["blocks_activation"] and not metadata["modified"]
    assert metadata["snapshot"]["status"] == "GLOBAL_BLOCKED"
    assert metadata["snapshot"]["source_format"] == "legacy"
    report = deployment_preflight.audit_deployment(
        project_root=Path(__file__).resolve().parents[1], runtime_root=root, archive_root=None
    )
    assert report["status"] == "blocked" and report["decision"] == "BLOCKED_PREFLIGHT_FAILURE"
    assert "authority_global_blocked" in report["failures"]
    assert files(root) == before


@pytest.mark.parametrize("reader", ["risk", "guard", "l1"])
def test_state_altered_during_read_is_never_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root, path = fixture_runtime(tmp_path, structured())
    original = canonical.os.fstat
    changed = False

    def change(fd: int) -> os.stat_result:
        nonlocal changed
        result = original(fd)
        if not changed:
            changed = True
            path.write_bytes(b'{"enabled":true,"runtime_mode":"paper"}')
        return result

    monkeypatch.setattr(canonical.os, "fstat", change)
    if reader == "l1":
        with pytest.raises(authority.AuthorityDenied, match="authority_changed_during_read"):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("BTCUSDT",))
    elif reader == "risk":
        assert risk.load_kill_switch(path)["reason"] == "kill_switch_changed_during_read"
    else:
        assert canonical.inspect_kill_switch(path).reason == "kill_switch_changed_during_read"


@pytest.mark.parametrize("reader", ["risk", "guard", "l1"])
def test_permission_denial_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root, path = fixture_runtime(tmp_path, structured())
    original = Path.open

    def denied(target: Path, *args: Any, **kwargs: Any) -> Any:
        if target == path:
            raise PermissionError("fixture_secret_not_in_report")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    if reader == "l1":
        with pytest.raises(authority.AuthorityDenied, match="authority_read_PermissionError"):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("BTCUSDT",))
    else:
        result = (
            risk.load_kill_switch(path)
            if reader == "risk"
            else canonical.inspect_kill_switch(path).to_dict()
        )
        assert "fixture_secret" not in json.dumps(result)
        assert result["status"] == canonical.CORRUPTED


@pytest.mark.parametrize("legacy", [True, False])
def test_operator_preserves_existing_format_and_symbol_halts(tmp_path: Path, legacy: bool) -> None:
    value = (
        {"enabled": True, "runtime_mode": "paper"}
        if legacy
        else structured(symbols={"ETHUSDT": entry(True)})
    )
    root, path = fixture_runtime(tmp_path, value)
    activated = risk.set_kill_switch(True, "manual fixture halt", path)
    assert ("enabled" in activated) == legacy
    assert ("global" in activated) != legacy
    assert canonical.inspect_kill_switch(path, "ETHUSDT").status == canonical.GLOBAL_BLOCKED
    cleared = risk.set_kill_switch(False, "manual fixture clear", path)
    assert ("enabled" in cleared) == legacy
    assert not list(path.parent.glob(".*.tmp"))
    if legacy:
        assert canonical.inspect_kill_switch(path).block_operation
        with pytest.raises(
            authority.AuthorityDenied, match="authority_legacy_clear_not_authorization"
        ):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("ETHUSDT",))
    else:
        assert canonical.inspect_kill_switch(path, "ETHUSDT").status == canonical.SYMBOL_BLOCKED
        assert cleared["symbols"] == value["symbols"]
        assert canonical.inspect_kill_switch(path, "BTCUSDT").status == canonical.CLEAR


def test_legacy_guard_operator_does_not_migrate_or_create_second_authority(tmp_path: Path) -> None:
    _, path = fixture_runtime(tmp_path, {"enabled": True, "runtime_mode": "paper"})
    guard = canonical.KillSwitchGuard(
        state_path=path, event_log_path=tmp_path / "fixture-events.jsonl"
    )
    guard.activate_global(reason="manual fixture halt", actor="pytest")
    assert "global" not in json.loads(path.read_bytes())
    guard.clear_global(reason="manual fixture clear", actor="pytest")
    assert guard.inspect().block_operation
    before = path.read_bytes()
    with pytest.raises(canonical.KillSwitchStateError, match="explicit_migration_required"):
        guard.activate_symbol("BTCUSDT", reason="fixture", actor="pytest")
    assert path.read_bytes() == before


def test_operator_atomic_replace_failure_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, path = fixture_runtime(tmp_path, structured())
    before = path.read_bytes()

    def denied(target: Path, destination: Path) -> None:
        assert path.read_bytes() == before
        assert json.loads(target.read_bytes())["global"]["enabled"]
        raise PermissionError("fixture")

    monkeypatch.setattr(Path, "replace", denied)
    with pytest.raises(canonical.KillSwitchStateError, match="state_write_failed:PermissionError"):
        risk.set_kill_switch(True, "fixture halt", path)
    assert path.read_bytes() == before and not list(path.parent.glob(".*.tmp"))


def test_operator_initialization_is_explicit_and_legacy_compatible(tmp_path: Path) -> None:
    path = tmp_path / "operator.json"
    assert risk.load_kill_switch(path)["enabled"] and not path.exists()
    value = risk.set_kill_switch(False, "manual fixture clear", path)
    assert value["enabled"] is False and "schema_version" not in value
    assert risk.load_kill_switch(path)["enabled"] is True


def test_default_execution_consumer_uses_one_canonical_path() -> None:
    assert (
        inspect.signature(OrderManager).parameters["kill_switch_path"].default
        == canonical.DEFAULT_KILL_SWITCH_PATH
    )


def test_timestamp_comparison_is_chronological_not_lexicographic(tmp_path: Path) -> None:
    _, path = fixture_runtime(
        tmp_path, structured(**{"global": entry(updated_at="2026-07-01T00:00:00.100000Z")})
    )
    assert canonical.inspect_kill_switch(path).status == canonical.CORRUPTED


def test_live_flags_still_prevent_operator_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, path = fixture_runtime(tmp_path, structured())
    before = path.read_bytes()
    monkeypatch.setenv("LIVE_ENABLED", "true")
    with pytest.raises(canonical.KillSwitchSafetyError):
        risk.set_kill_switch(False, "fixture", path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("reader", ["risk", "guard", "l1"])
def test_symlink_or_reparse_state_cannot_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root, path = fixture_runtime(tmp_path, structured())
    lstat = Path.lstat

    def linked(target: Path) -> os.stat_result:
        original = lstat(target)
        if target == path:
            import stat

            return os.stat_result((stat.S_IFLNK | 0o777, *original[1:]))
        return original

    monkeypatch.setattr(Path, "lstat", linked)
    if reader == "l1":
        with pytest.raises(authority.AuthorityDenied, match="reparse_or_symlink_forbidden"):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("BTCUSDT",))
    elif reader == "risk":
        assert risk.load_kill_switch(path)["enabled"] is True
    else:
        assert canonical.inspect_kill_switch(path).block_operation


@pytest.mark.parametrize("reader", ["risk", "guard", "l1"])
def test_shared_read_deadline_never_allows_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    root, path = fixture_runtime(tmp_path, structured())
    clock = iter([0.0, canonical.STATE_READ_DEADLINE_SECONDS + 1.0])
    monkeypatch.setattr(canonical.time, "monotonic", lambda: next(clock))
    if reader == "l1":
        with pytest.raises(authority.AuthorityDenied, match="authority_read_deadline"):
            authority.read_authority(root, root / authority.LEDGER_RELATIVE, ("BTCUSDT",))
    elif reader == "risk":
        assert risk.load_kill_switch(path)["reason"] == "kill_switch_read_deadline"
    else:
        assert canonical.inspect_kill_switch(path).reason == "kill_switch_read_deadline"


@pytest.mark.parametrize("stage", ["json", "normalization", "snapshot"])
@pytest.mark.parametrize("elapsed", [0.75, 1.0])
def test_post_read_processing_deadline_never_returns_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, elapsed: float
) -> None:
    _, path = fixture_runtime(tmp_path, structured())
    before = path.read_bytes()
    clock = {"elapsed": 0.0}
    monkeypatch.setattr(canonical.time, "monotonic", lambda: clock["elapsed"])
    owner, name = (
        (canonical.json, "loads") if stage == "json"
        else (canonical, "normalize_state" if stage == "normalization" else "KillSwitchSnapshot")
    )
    original = getattr(owner, name)

    def delayed(*args: Any, **kwargs: Any) -> Any:
        value = original(*args, **kwargs)
        clock["elapsed"] = elapsed
        return value

    monkeypatch.setattr(owner, name, delayed)
    with pytest.raises(canonical.KillSwitchStateError, match="kill_switch_read_deadline"):
        canonical.read_kill_switch_snapshot(path)
    assert path.read_bytes() == before


def test_processing_below_deadline_still_returns_explicit_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, path = fixture_runtime(tmp_path, structured())
    clock = iter([0.0, 0.1, canonical.STATE_READ_DEADLINE_SECONDS - 0.001])
    monkeypatch.setattr(canonical.time, "monotonic", lambda: next(clock))
    assert canonical.read_kill_switch_snapshot(path).result("BTCUSDT").status == canonical.CLEAR


@pytest.mark.parametrize("symbol", ["", "  ", "BTC USDT"])
def test_invalid_requested_identity_blocks_safety_readers(tmp_path: Path, symbol: str) -> None:
    _, path = fixture_runtime(tmp_path, structured())
    assert risk.load_kill_switch(path, symbol=symbol)["enabled"] is True
    assert canonical.inspect_kill_switch(path, symbol).block_operation


def test_symbol_operator_on_missing_state_does_not_invent_global_clear(tmp_path: Path) -> None:
    guard = canonical.KillSwitchGuard(
        state_path=tmp_path / "kill.json", event_log_path=tmp_path / "events.jsonl"
    )
    guard.activate_symbol("BTCUSDT", reason="fixture_symbol_halt", actor="pytest")
    assert guard.inspect("ETHUSDT").status == canonical.GLOBAL_BLOCKED
