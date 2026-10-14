from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from scripts.audit_paper_signal_riskmanager_runtime_wiring_v1 import build_audit_report
from smartcrypto import bot
from smartcrypto.execution.signal_producer import build_active_signals
from smartcrypto.execution.signal_risk_gate import apply_risk_manager_gate
from smartcrypto.risk import kill_switch_guard as canonical
from smartcrypto.risk.risk_manager import RiskLimits, RiskManager
from smartcrypto.settings import RuntimeSettings
from smartcrypto.ops.paper_signal_riskmanager_runtime_wiring_audit.audit import (
    KNOWN_OUT_OF_SCOPE_WRITERS,
    READER_MODULE,
    WRITER_MODULES,
    _HARDCODED_APPROVAL_PATTERN,
    audit_reader_source,
    audit_writer_source,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_RISK_LIMITS_PATH = PROJECT_ROOT / "config" / "risk_limits.yml"


def _authority_payload(enabled: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime_mode": "paper",
        "updated_at": "2026-07-01T00:00:00Z",
        "global": {
            "enabled": enabled,
            "reason": "explicit_test_authority",
            "actor": "pytest",
            "updated_at": "2026-07-01T00:00:00Z",
        },
        "symbols": {},
    }


def _write_clear_authority(root: Path) -> Path:
    path = root / canonical.DEFAULT_KILL_SWITCH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(_authority_payload()), encoding="utf-8")
    return path


@pytest.fixture
def clear_authority(tmp_path: Path) -> Path:
    return _write_clear_authority(tmp_path)


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LIVE_ENABLED", "ORDER_SUBMISSION_ENABLED", "REAL_ORDER_SUBMISSION_ENABLED"):
        monkeypatch.setenv(name, "false")


def _valid_signal(symbol: str = "BTCUSDT") -> dict[str, object]:
    return {
        "pair": f"{symbol[:-4]}/USDT:USDT",
        "symbol": symbol,
        "side": "long",
        "proposed_side": "long",
        "risk_approved": True,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"enabled": True, "runtime_mode": "paper"},
        {"enabled": False, "runtime_mode": "paper"},
        _authority_payload(True),
        {**_authority_payload(), "enabled": False},
        canonical.default_state(),
        b'{"schema_version":1,"global":',
        None,
    ],
    ids=["legacy_halt", "legacy_clear", "structured_halt", "contradictory", "default_clear", "partial", "missing"],
)
def test_canonical_authority_cannot_be_bypassed_by_yaml_or_injected_manager(
    clear_authority: Path, payload: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    if payload is None:
        clear_authority.unlink()
    else:
        clear_authority.write_bytes(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    before = clear_authority.read_bytes() if clear_authority.exists() else None
    manager = RiskManager(RiskLimits(kill_switch_enabled=False))

    def forbidden(signals: object) -> None:
        pytest.fail("blocked authority reached RiskManager approval")

    monkeypatch.setattr(manager, "approve_many", forbidden)
    result = apply_risk_manager_gate(
        [_valid_signal()], risk_manager=manager, kill_switch_path=clear_authority
    )
    assert result.status == "blocked" and result.signals_approved == 0
    assert result.approved_signals == [] and result.rejected_signals[0]["risk_approved"] is False
    assert result.reason.startswith("canonical_kill_switch:")
    assert (clear_authority.read_bytes() if clear_authority.exists() else None) == before


@pytest.mark.parametrize("blocked_identity", ["BTCUSDT", "BTC/USDT:USDT", "BTC/USDT"])
def test_symbol_halt_respects_both_pair_and_symbol_without_blocking_other_symbol(
    clear_authority: Path, blocked_identity: str
) -> None:
    payload = _authority_payload()
    payload["symbols"] = {blocked_identity: _authority_payload(True)["global"]}
    clear_authority.write_text(json.dumps(payload), encoding="utf-8")
    result = apply_risk_manager_gate(
        [_valid_signal(), _valid_signal("ETHUSDT")],
        risk_limits_path=REAL_RISK_LIMITS_PATH,
        kill_switch_path=clear_authority,
    )
    assert result.status == "ok"
    assert [signal["symbol"] for signal in result.approved_signals] == ["ETHUSDT"]
    assert result.rejected_signals[0]["risk_approved"] is False
    assert "canonical_kill_switch:SYMBOL_BLOCKED" in result.rejected_signals[0]["risk_reasons"]


@pytest.mark.parametrize("change", ["global", "symbol", "missing", "corrupt"])
def test_revocation_during_risk_evaluation_discards_earlier_approval(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    manager = RiskManager(RiskLimits())
    approve = manager.approve_many

    def revoke(signals):
        decisions = approve(signals)
        if change == "missing":
            clear_authority.unlink()
        elif change == "corrupt":
            clear_authority.write_bytes(b"{")
        else:
            payload = _authority_payload(change == "global")
            if change == "symbol":
                payload["symbols"] = {"BTCUSDT": _authority_payload(True)["global"]}
            clear_authority.write_text(json.dumps(payload), encoding="utf-8")
        return decisions

    monkeypatch.setattr(manager, "approve_many", revoke)
    result = apply_risk_manager_gate(
        [_valid_signal()], risk_manager=manager, kill_switch_path=clear_authority
    )
    assert result.signals_approved == 0 and result.approved_signals == []
    if change == "symbol":
        assert result.status == "ok"
        assert "canonical_kill_switch:SYMBOL_BLOCKED" in result.rejected_signals[0]["risk_reasons"]
    else:
        assert result.status == "blocked"
        assert result.reason.startswith("canonical_kill_switch:")
        assert result.canonical_kill_switch["status"] != canonical.CLEAR
    assert result.rejected_signals[0]["risk_approved"] is False


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_authority_io_error_is_sanitized_and_fail_closed(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    original = Path.open

    def denied(path: Path, *args, **kwargs):
        if path == clear_authority:
            raise error("secret_must_not_be_reported")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    result = apply_risk_manager_gate(
        [_valid_signal()], risk_limits_path=REAL_RISK_LIMITS_PATH, kill_switch_path=clear_authority
    )
    assert result.status == "blocked" and result.signals_approved == 0
    assert "secret_must_not_be_reported" not in json.dumps(result.to_dict())
    assert result.canonical_kill_switch["status"] == canonical.CORRUPTED


def test_pure_risk_manager_has_no_implicit_authority_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("pure RiskManager acquired an implicit filesystem dependency")

    monkeypatch.setattr(canonical, "read_kill_switch_snapshot", forbidden)
    assert RiskManager(RiskLimits()).approve(_valid_signal()).approved


def test_missing_default_canonical_path_never_creates_clear_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = apply_risk_manager_gate(
        [_valid_signal()], risk_limits_path=REAL_RISK_LIMITS_PATH
    )
    assert result.status == "blocked" and result.signals_approved == 0
    assert result.canonical_kill_switch["path"] == canonical.DEFAULT_KILL_SWITCH_PATH
    assert not list(tmp_path.iterdir())


def test_default_gate_obeys_canonical_halt_when_yaml_is_clear(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(clear_authority.parents[2])
    clear_authority.write_text(
        json.dumps({"enabled": True, "runtime_mode": "paper"}), encoding="utf-8"
    )
    before = clear_authority.read_bytes()
    result = apply_risk_manager_gate([_valid_signal()], risk_limits_path=REAL_RISK_LIMITS_PATH)
    assert result.status == "blocked" and result.signals_approved == 0
    assert result.reason == "canonical_kill_switch:GLOBAL_BLOCKED"
    assert result.canonical_kill_switch["source_format"] == "legacy"
    assert clear_authority.read_bytes() == before


@pytest.mark.parametrize("enabled", [True, False])
def test_secondary_paper_writer_reuses_authority_before_publication(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    root = clear_authority.parents[2]
    monkeypatch.chdir(root)
    monkeypatch.setattr(bot, "DEFAULT_RISK_LIMITS_PATH", REAL_RISK_LIMITS_PATH)
    monkeypatch.setattr(bot, "_load_predictions", lambda _: [_valid_signal()])
    clear_authority.write_text(json.dumps(_authority_payload(enabled)), encoding="utf-8")
    before = clear_authority.read_bytes()
    output = root / "signals.json"
    settings = RuntimeSettings(
        runtime_mode="paper",
        live_enabled=False,
        order_submission_enabled=False,
        real_order_submission_enabled=False,
        database_path=root / "unused.sqlite",
        signals_path=output,
        predictions_path=root / "unused.parquet",
    )
    if enabled:
        with pytest.raises(RuntimeError, match="signal_risk_gate_blocked:canonical_kill_switch:GLOBAL_BLOCKED"):
            bot.run_once(settings)
        assert not output.exists()
    else:
        bot.run_once(settings)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["signals"][0]["risk_approved"] is True
        assert payload["signals"][0]["symbol"] == "BTCUSDT"
    assert clear_authority.read_bytes() == before
    assert not settings.database_path.exists() and not settings.predictions_path.exists()


@pytest.mark.parametrize("identity", [None, "", "btc usdt", 123, "ETHUSDT"])
def test_invalid_or_mismatched_signal_identity_cannot_bypass_scope(
    clear_authority: Path, identity: object
) -> None:
    signal = _valid_signal()
    signal["symbol"] = identity
    if identity is None:
        signal.pop("pair")
    result = apply_risk_manager_gate(
        [signal], risk_manager=RiskManager(RiskLimits()), kill_switch_path=clear_authority
    )
    assert result.signals_approved == 0
    assert result.rejected_signals[0]["risk_approved"] is False
    assert any(reason.startswith("canonical_kill_switch:") for reason in result.rejected_signals[0]["risk_reasons"])


def test_revocation_before_evaluation_cannot_be_undone_inside_manager(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _authority_payload()
    payload["symbols"] = {"BTCUSDT": _authority_payload(True)["global"]}
    clear_authority.write_text(json.dumps(payload), encoding="utf-8")
    manager = RiskManager(RiskLimits())
    approve = manager.approve_many

    def clear(signals):
        clear_authority.write_text(json.dumps(_authority_payload()), encoding="utf-8")
        return approve(signals)

    monkeypatch.setattr(manager, "approve_many", clear)
    result = apply_risk_manager_gate(
        [_valid_signal()], risk_manager=manager, kill_switch_path=clear_authority
    )
    assert result.signals_approved == 0
    assert "canonical_kill_switch:SYMBOL_BLOCKED" in result.rejected_signals[0]["risk_reasons"]


# ---------------------------------------------------------------------------
# Static source wiring: every writer Freqtrade's strategy actually reads from
# must import and call apply_risk_manager_gate, and must not hardcode
# risk_approved itself.
# ---------------------------------------------------------------------------


def test_signal_producer_writer_is_wired_to_risk_gate() -> None:
    check = audit_writer_source(PROJECT_ROOT, "smartcrypto/execution/signal_producer.py")
    assert check.exists is True
    assert check.imports_risk_gate is True
    assert check.calls_apply_risk_manager_gate is True
    assert check.hardcoded_risk_approved_true_found is False
    assert check.wired is True


def test_signal_exporter_writer_is_wired_to_risk_gate() -> None:
    check = audit_writer_source(PROJECT_ROOT, "smartcrypto/qlib_engine/signal_exporter.py")
    assert check.exists is True
    assert check.imports_risk_gate is True
    assert check.calls_apply_risk_manager_gate is True
    assert check.hardcoded_risk_approved_true_found is False
    assert check.wired is True


def test_signal_contract_guard_writer_is_wired_to_risk_gate() -> None:
    check = audit_writer_source(PROJECT_ROOT, "smartcrypto/execution/signal_contract_guard.py")
    assert check.exists is True
    assert check.imports_risk_gate is True
    assert check.calls_apply_risk_manager_gate is True
    assert check.hardcoded_risk_approved_true_found is False
    assert check.wired is True


def test_reader_strategy_is_wired_to_risk_gate() -> None:
    check = audit_reader_source(PROJECT_ROOT, READER_MODULE)
    assert check.exists is True
    assert check.strict_boolean_check_found is True
    assert check.fallback_iterates_all_paths is True
    assert check.wired is True


def test_market_signal_exporter_remains_out_of_scope_but_cannot_forge_approval() -> None:
    # NOT part of this branch's fix scope (see smartcrypto/ops/
    # paper_signal_riskmanager_runtime_wiring_audit/audit.py module
    # docstring): it is not wired into docker-compose.paper.yml's
    # continuously-running services. This test asserts the limitation is
    # still real (so the audit report is not silently stale) and that it is
    # tracked, not hidden.
    assert KNOWN_OUT_OF_SCOPE_WRITERS == ("smartcrypto/execution/market_signal_exporter.py",)
    check = audit_writer_source(PROJECT_ROOT, KNOWN_OUT_OF_SCOPE_WRITERS[0])
    assert check.exists is True
    assert check.hardcoded_risk_approved_true_found is False
    assert check.wired is False


def test_writer_files_do_not_contain_hardcoded_risk_approved_literal() -> None:
    # Regression guard independent of the audit module's own regex: reads
    # each in-scope writer directly and fails if a hardcoded
    # "risk_approved": True is ever reintroduced.
    for relpath in WRITER_MODULES:
        text = (PROJECT_ROOT / relpath).read_text(encoding="utf-8")
        assert not _HARDCODED_APPROVAL_PATTERN.search(text), f"hardcoded risk_approved found in {relpath}"


# ---------------------------------------------------------------------------
# Dynamic gate behavior: apply_risk_manager_gate must fail closed on any
# RiskManager unavailability/exception, and must never let a rejected signal
# through as approved.
# ---------------------------------------------------------------------------


def test_gate_fail_closed_on_missing_risk_limits_config(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist_risk_limits.yml"
    result = apply_risk_manager_gate(
        [{"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long", "score": 0.9}],
        risk_limits_path=missing_path,
    )
    assert result.status == "blocked"
    assert result.risk_manager_available is False
    assert result.signals_approved == 0
    assert result.approved_signals == []
    assert result.rejected_signals[0]["risk_approved"] is False


def test_gate_fail_closed_on_risk_manager_exception(clear_authority: Path) -> None:
    class _RaisingRiskManager:
        def approve_many(self, signals):
            raise RuntimeError("simulated_failure")

    result = apply_risk_manager_gate(
        [{"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long", "score": 0.9}],
        risk_manager=_RaisingRiskManager(),
        kill_switch_path=clear_authority,
    )
    assert result.status == "blocked"
    assert result.signals_approved == 0
    assert result.approved_signals == []
    assert result.rejected_signals[0]["risk_approved"] is False


def test_gate_rejected_signals_excluded_from_approved_and_stamped_false(clear_authority: Path) -> None:
    from smartcrypto.risk.risk_manager import SignalRiskDecision

    class _MixedRiskManager:
        def approve_many(self, signals):
            decisions = []
            for signal in signals:
                approved = signal.get("side") == "long"
                decisions.append(
                    SignalRiskDecision(
                        approved=approved,
                        status="approved" if approved else "blocked",
                        reasons=[] if approved else ["stub_rejects_non_long"],
                        signal=dict(signal),
                        created_at="2026-07-04T00:00:00+00:00",
                    )
                )
            return decisions

    candidates = [
        {"pair": "ETH/USDT:USDT", "symbol": "ETHUSDT", "side": "long", "score": 0.8},
        {"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "short", "score": -0.7},
    ]
    result = apply_risk_manager_gate(
        candidates, risk_manager=_MixedRiskManager(), kill_switch_path=clear_authority
    )

    assert result.status == "ok"
    assert result.signals_approved == 1
    assert result.signals_rejected == 1
    assert {item["symbol"] for item in result.approved_signals} == {"ETHUSDT"}
    assert all(item["risk_approved"] is True for item in result.approved_signals)
    assert {item["symbol"] for item in result.rejected_signals} == {"BTCUSDT"}
    assert all(item["risk_approved"] is False for item in result.rejected_signals)


def test_gate_forces_risk_approved_false_even_if_candidate_forged_true(clear_authority: Path) -> None:
    # DOGE/USDT:USDT is not in config/risk_limits.yml's allowed_pairs, so the
    # real RiskManager must reject it - even though the candidate signal
    # itself already carries a forged risk_approved=True, exactly mirroring
    # the shape of the original bug this branch fixes.
    candidates = [
        {
            "pair": "DOGE/USDT:USDT",
            "symbol": "DOGEUSDT",
            "side": "long",
            "proposed_side": "long",
            "score": 0.99,
            "risk_approved": True,
        }
    ]
    result = apply_risk_manager_gate(
        candidates, risk_limits_path=REAL_RISK_LIMITS_PATH, kill_switch_path=clear_authority
    )

    assert result.status == "ok"
    assert result.signals_approved == 0
    assert result.approved_signals == []
    assert result.rejected_signals[0]["risk_approved"] is False
    assert any("pair_not_allowed" in reason for reason in result.rejected_signals[0]["risk_reasons"])


def test_gate_rejects_signal_without_proposed_side(clear_authority: Path) -> None:
    candidates = [
        {"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long"}
    ]
    result = apply_risk_manager_gate(
        candidates, risk_limits_path=REAL_RISK_LIMITS_PATH, kill_switch_path=clear_authority
    )

    assert result.status == "ok"
    assert result.signals_approved == 0
    assert result.rejected_signals[0]["risk_approved"] is False
    assert "proposed_side_missing_or_invalid" in result.rejected_signals[0]["risk_reasons"]


def test_gate_approves_allowed_pair_with_real_risk_manager(clear_authority: Path) -> None:
    candidates = [
        {
            "pair": "BTC/USDT:USDT",
            "symbol": "BTCUSDT",
            "side": "long",
            "proposed_side": "long",
            "score": -0.9,
        }
    ]
    result = apply_risk_manager_gate(
        candidates, risk_limits_path=REAL_RISK_LIMITS_PATH, kill_switch_path=clear_authority
    )

    assert result.status == "ok"
    assert result.signals_approved == 1
    assert result.approved_signals[0]["risk_approved"] is True
    assert result.approved_signals[0]["risk_manager_source"] == "smartcrypto.risk.risk_manager.RiskManager"


# ---------------------------------------------------------------------------
# End-to-end: build_active_signals() (the function Docker's
# qlib-refresh-supervisor-paper service actually calls) must only ever write
# RiskManager-approved signals to the files Freqtrade reads.
#
# Predictions are written as CSV rather than Parquet: load_predictions()
# supports both, and CSV keeps this suite runnable without the optional
# pyarrow/fastparquet dependency, which is not installed in every dev/CI
# sandbox.
# ---------------------------------------------------------------------------


def test_signal_producer_end_to_end_only_writes_risk_approved_signals(
    monkeypatch, tmp_path: Path, clear_authority: Path
) -> None:
    project_probe = PROJECT_ROOT / "data/reports/qlib_v3_natural_admission_lineage_probe_v1.jsonl"
    before_probe = project_probe.read_bytes() if project_probe.exists() else None
    monkeypatch.chdir(tmp_path)
    predictions_path = tmp_path / "predictions.csv"
    primary_path = tmp_path / "freqtrade_signals.json"
    pinned_path = tmp_path / "active_freqtrade_signals.json"
    report_path = tmp_path / "phase13_signal_producer_report.json"

    pd.DataFrame(
        [
            {"symbol": "ETHUSDT", "prob_up": 0.9},
            {"symbol": "BTCUSDT", "prob_up": 0.1},
            {"symbol": "DOGEUSDT", "prob_up": 0.95},
        ]
    ).to_csv(predictions_path, index=False)

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.inspect_qlib_prediction_freshness",
        lambda *args, **kwargs: {"freshness_status": "fresh", "input_data_status": "input_data_fresh", "rows": 3},
    )

    report = build_active_signals(
        {
            "runtime_mode": "paper",
            "paths": {
                "predictions": str(predictions_path),
                "primary_signals": str(primary_path),
                "pinned_signals": str(pinned_path),
                "report": str(report_path),
                "risk_limits": str(REAL_RISK_LIMITS_PATH),
                "kill_switch": str(clear_authority),
            },
            "policy": {
                "long_probability": 0.55,
                "short_probability": 0.45,
                "min_confidence": 0.0,
                "max_signals": 5,
                "top_n_telemetry": 5,
                "never_overwrite_with_empty": False,
            },
            "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
        },
        force_from_predictions=True,
    )

    payload = json.loads(primary_path.read_text(encoding="utf-8"))
    written_symbols = {item["symbol"] for item in payload["signals"]}

    assert written_symbols == {"ETHUSDT", "BTCUSDT"}
    assert "DOGEUSDT" not in written_symbols
    assert all(item["risk_approved"] is True for item in payload["signals"])
    gate = report["risk_manager_gate"]
    assert gate["signals_approved"] == 2
    assert gate["signals_rejected"] == 1
    assert (project_probe.read_bytes() if project_probe.exists() else None) == before_probe


def test_signal_producer_never_overwrites_with_empty_when_all_rejected(
    monkeypatch, tmp_path: Path, clear_authority: Path
) -> None:
    project_probe = PROJECT_ROOT / "data/reports/qlib_v3_natural_admission_lineage_probe_v1.jsonl"
    before_probe = project_probe.read_bytes() if project_probe.exists() else None
    monkeypatch.chdir(tmp_path)
    predictions_path = tmp_path / "predictions.csv"
    primary_path = tmp_path / "freqtrade_signals.json"
    pinned_path = tmp_path / "active_freqtrade_signals.json"
    report_path = tmp_path / "phase13_signal_producer_report.json"

    previous_payload = {
        "generated_at": "2026-07-03T00:00:00+00:00",
        "source": "previous_cycle",
        "model_version": "qlib_lgbm_v1",
        "runtime_mode": "paper",
        "signals": [{"pair": "ETH/USDT:USDT", "symbol": "ETHUSDT", "side": "long", "risk_approved": True}],
    }
    primary_path.write_text(json.dumps(previous_payload), encoding="utf-8")

    pd.DataFrame([{"symbol": "DOGEUSDT", "prob_up": 0.95}]).to_csv(
        predictions_path, index=False
    )

    monkeypatch.setattr(
        "smartcrypto.execution.signal_producer.inspect_qlib_prediction_freshness",
        lambda *args, **kwargs: {"freshness_status": "fresh", "input_data_status": "input_data_fresh", "rows": 1},
    )

    report = build_active_signals(
        {
            "runtime_mode": "paper",
            "paths": {
                "predictions": str(predictions_path),
                "primary_signals": str(primary_path),
                "pinned_signals": str(pinned_path),
                "report": str(report_path),
                "risk_limits": str(REAL_RISK_LIMITS_PATH),
                "kill_switch": str(clear_authority),
            },
            "policy": {
                "long_probability": 0.55,
                "short_probability": 0.45,
                "min_confidence": 0.0,
                "max_signals": 5,
                "top_n_telemetry": 5,
                "never_overwrite_with_empty": True,
            },
            "risk": {"max_position_usdt": 50.0, "leverage": 2.0},
        },
        force_from_predictions=False,
    )

    assert report["written_primary"] is False
    assert report["reason"] == "no_signals_generated_and_never_overwrite_with_empty_enabled"
    unchanged = json.loads(primary_path.read_text(encoding="utf-8"))
    assert unchanged == previous_payload
    assert (project_probe.read_bytes() if project_probe.exists() else None) == before_probe


# ---------------------------------------------------------------------------
# Audit report and CLI: the auditor itself must report "ok" only when every
# writer and the reader are actually wired, and must never assert an unsafe
# safety flag.
# ---------------------------------------------------------------------------


def test_build_audit_report_status_ok_when_fully_wired(
    clear_authority: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(clear_authority.parents[2])
    report = build_audit_report(project_root=PROJECT_ROOT)

    assert report["status"] == "ok"
    assert report["all_writers_wired"] is True
    assert report["reader_wired"] is True
    assert report["gate_probes_pass"] is True
    assert report["validation_errors"] == []


def test_build_audit_report_safety_flags_never_permit_live_or_orders() -> None:
    report = build_audit_report(project_root=PROJECT_ROOT)
    flags = report["safety_flags"]

    assert flags["sends_orders"] is False
    assert flags["exchange_private_access"] is False
    assert flags["order_submission_enabled"] is False
    assert flags["real_order_submission_enabled"] is False
    assert flags["changes_risk"] is False
    assert flags["changes_model"] is False
    assert flags["writes_runtime"] is False
    assert flags["writes_sqlite"] is False
    assert flags["writes_parquet"] is False


def test_cli_script_json_output_reports_ok(clear_authority: Path) -> None:
    script = PROJECT_ROOT / "scripts" / "audit_paper_signal_riskmanager_runtime_wiring_v1.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", str(PROJECT_ROOT), "--json"],
        check=True,
        capture_output=True,
        text=True,
        cwd=clear_authority.parents[2],
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["all_writers_wired"] is True
    assert payload["reader_wired"] is True
    assert payload["gate_probes_pass"] is True
    assert payload["safety_flags"]["sends_orders"] is False
