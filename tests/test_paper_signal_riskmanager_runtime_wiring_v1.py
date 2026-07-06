from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

from scripts.audit_paper_signal_riskmanager_runtime_wiring_v1 import build_audit_report
from smartcrypto.execution.signal_producer import build_active_signals
from smartcrypto.execution.signal_risk_gate import apply_risk_manager_gate
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


def test_market_signal_exporter_is_documented_known_limitation() -> None:
    # NOT part of this branch's fix scope (see smartcrypto/ops/
    # paper_signal_riskmanager_runtime_wiring_audit/audit.py module
    # docstring): it is not wired into docker-compose.paper.yml's
    # continuously-running services. This test asserts the limitation is
    # still real (so the audit report is not silently stale) and that it is
    # tracked, not hidden.
    assert KNOWN_OUT_OF_SCOPE_WRITERS == ("smartcrypto/execution/market_signal_exporter.py",)
    check = audit_writer_source(PROJECT_ROOT, KNOWN_OUT_OF_SCOPE_WRITERS[0])
    assert check.exists is True
    assert check.hardcoded_risk_approved_true_found is True
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


def test_gate_fail_closed_on_risk_manager_exception() -> None:
    class _RaisingRiskManager:
        def approve_many(self, signals):
            raise RuntimeError("simulated_failure")

    result = apply_risk_manager_gate(
        [{"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long", "score": 0.9}],
        risk_manager=_RaisingRiskManager(),
    )
    assert result.status == "blocked"
    assert result.signals_approved == 0
    assert result.approved_signals == []
    assert result.rejected_signals[0]["risk_approved"] is False


def test_gate_rejected_signals_excluded_from_approved_and_stamped_false() -> None:
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
    result = apply_risk_manager_gate(candidates, risk_manager=_MixedRiskManager())

    assert result.status == "ok"
    assert result.signals_approved == 1
    assert result.signals_rejected == 1
    assert {item["symbol"] for item in result.approved_signals} == {"ETHUSDT"}
    assert all(item["risk_approved"] is True for item in result.approved_signals)
    assert {item["symbol"] for item in result.rejected_signals} == {"BTCUSDT"}
    assert all(item["risk_approved"] is False for item in result.rejected_signals)


def test_gate_forces_risk_approved_false_even_if_candidate_forged_true() -> None:
    # DOGE/USDT:USDT is not in config/risk_limits.yml's allowed_pairs, so the
    # real RiskManager must reject it - even though the candidate signal
    # itself already carries a forged risk_approved=True, exactly mirroring
    # the shape of the original bug this branch fixes.
    candidates = [
        {
            "pair": "DOGE/USDT:USDT",
            "symbol": "DOGEUSDT",
            "side": "long",
            "score": 0.99,
            "risk_approved": True,
        }
    ]
    result = apply_risk_manager_gate(candidates, risk_limits_path=REAL_RISK_LIMITS_PATH)

    assert result.status == "ok"
    assert result.signals_approved == 0
    assert result.approved_signals == []
    assert result.rejected_signals[0]["risk_approved"] is False
    assert any("pair_not_allowed" in reason for reason in result.rejected_signals[0]["risk_reasons"])


def test_gate_rejects_signal_in_neutral_score_zone_with_real_risk_manager() -> None:
    # config/risk_limits.yml: min_score_long=0.60, max_score_short=0.40.
    # A score of 0.5 is inside the neutral zone and must be rejected.
    candidates = [{"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long", "score": 0.5}]
    result = apply_risk_manager_gate(candidates, risk_limits_path=REAL_RISK_LIMITS_PATH)

    assert result.status == "ok"
    assert result.signals_approved == 0
    assert result.rejected_signals[0]["risk_approved"] is False
    assert "score_inside_neutral_zone" in result.rejected_signals[0]["risk_reasons"]


def test_gate_approves_allowed_pair_with_real_risk_manager() -> None:
    candidates = [{"pair": "BTC/USDT:USDT", "symbol": "BTCUSDT", "side": "long", "score": 0.9}]
    result = apply_risk_manager_gate(candidates, risk_limits_path=REAL_RISK_LIMITS_PATH)

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


def test_signal_producer_end_to_end_only_writes_risk_approved_signals(monkeypatch, tmp_path: Path) -> None:
    predictions_path = tmp_path / "predictions.csv"
    primary_path = tmp_path / "freqtrade_signals.json"
    pinned_path = tmp_path / "active_freqtrade_signals.json"
    report_path = tmp_path / "phase13_signal_producer_report.json"

    pd.DataFrame(
        [
            {"symbol": "ETHUSDT", "score": 0.9},  # allowed pair, long -> approved
            {"symbol": "BTCUSDT", "score": -0.8},  # allowed pair, short -> approved
            {"symbol": "DOGEUSDT", "score": 0.95},  # not in allowed_pairs -> rejected
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
            },
            "policy": {
                "min_abs_score": 0.0,
                "min_confidence": 0.0,
                "max_signals": 5,
                "include_top_n_when_threshold_empty": 5,
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


def test_signal_producer_never_overwrites_with_empty_when_all_rejected(monkeypatch, tmp_path: Path) -> None:
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

    pd.DataFrame([{"symbol": "DOGEUSDT", "score": 0.95}]).to_csv(predictions_path, index=False)

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
            },
            "policy": {
                "min_abs_score": 0.0,
                "min_confidence": 0.0,
                "max_signals": 5,
                "include_top_n_when_threshold_empty": 5,
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


# ---------------------------------------------------------------------------
# Audit report and CLI: the auditor itself must report "ok" only when every
# writer and the reader are actually wired, and must never assert an unsafe
# safety flag.
# ---------------------------------------------------------------------------


def test_build_audit_report_status_ok_when_fully_wired() -> None:
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


def test_cli_script_json_output_reports_ok() -> None:
    script = PROJECT_ROOT / "scripts" / "audit_paper_signal_riskmanager_runtime_wiring_v1.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", str(PROJECT_ROOT), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["status"] == "ok"
    assert payload["all_writers_wired"] is True
    assert payload["reader_wired"] is True
    assert payload["gate_probes_pass"] is True
    assert payload["safety_flags"]["sends_orders"] is False
