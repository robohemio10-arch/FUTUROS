from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.execution.paper_candidate_trade_lineage_propagation_v1 import (
    build_authoritative_signal_identity,
    extract_explicit_decision_event_id,
    project_closed_paper_trade_link_readonly,
    project_strict_decision,
)


MODULE_PATH = Path(
    "smartcrypto/execution/"
    "paper_candidate_trade_lineage_propagation_v1/"
    "trade_projection.py"
)


def _strict_decision(*, side: str = "long"):
    source_signal = {
        "candidate_id": "candidate-1",
        "signal_id": "signal:phase13-signal-producer:abc123",
        "correlation_id": "correlation:abc123",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": side,
    }
    identity = build_authoritative_signal_identity(source_signal)

    decision_fields: dict[str, Any] = {
        "runtime_mode": "paper",
        "pair": "BTC/USDT:USDT",
        "symbol": "BTCUSDT",
        "side": side,
        "feature_timestamp": datetime(
            2026,
            8,
            21,
            18,
            0,
            0,
            tzinfo=timezone.utc,
        ),
        "decision_timestamp": datetime(
            2026,
            8,
            21,
            18,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        "risk_checked_at_utc": datetime(
            2026,
            8,
            21,
            18,
            0,
            1,
            tzinfo=timezone.utc,
        ),
        "feature_contract_version": "feature-contract-v1",
        "feature_hash": "b" * 64,
        "model_id": "qlib-ranking-model",
        "model_version": "qlib-test-v1",
        "model_hash": "c" * 64,
        "qlib_score": 0.90,
        "calibrated_probability": 0.72,
        "expected_net_pnl": 0.12,
        "fast_stop_probability": 0.18,
        "regime": "normal",
        "alignment": "aligned",
        "ai_shadow_decision": "NOT_EVALUATED",
        "ai_shadow_reasons": (),
        "risk_approved": True,
        "risk_reasons": (),
        "risk_policy_id": "risk:test",
        "risk_config_hash": "a" * 64,
        "approved_stake_usdt": 50.0,
        "approved_leverage": 2.0,
        "final_decision": "ALLOW",
        "final_reasons": ("risk_manager_approved",),
        "source_signal_sha256": identity.source_signal_sha256,
    }
    return project_strict_decision(identity, decision_fields)


def _trade_row(decision, **overrides: Any) -> dict[str, Any]:
    side = decision.decision_projection.target_payload.side.value
    row = {
        "id": 123,
        "pair": "BTC/USDT:USDT",
        "is_short": 1 if side == "short" else 0,
        "is_open": 0,
        "open_date": "2026-08-21T18:00:03+00:00",
        "enter_tag": (
            f"smartcrypto_{side}|decision_event_id="
            f"{decision.decision_projection.target_payload.event_id}"
        ),
    }
    row.update(overrides)
    return row


def test_exact_closed_paper_trade_projects_strict_trade_link() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision),
        source_database_sha256="d" * 64,
    )

    assert outcome.report.status == "ok"
    assert outcome.report.projection_created is True
    assert outcome.projection is not None

    target = outcome.projection.trade_link_projection.target_payload
    decision_target = decision.decision_projection.target_payload

    assert target.trade_id == 123
    assert target.parent_event_id == decision_target.event_id
    assert target.candidate_id == decision_target.candidate_id
    assert target.signal_id == decision_target.signal_id
    assert target.correlation_id == decision_target.correlation_id
    assert target.decision_payload_sha256 == decision_target.payload_sha256


def test_projection_is_deterministic_for_same_trade_observation() -> None:
    decision = _strict_decision()
    kwargs = {
        "decision": decision,
        "trade_row": _trade_row(decision),
        "source_database_sha256": "d" * 64,
    }

    first = project_closed_paper_trade_link_readonly(**kwargs)
    second = project_closed_paper_trade_link_readonly(**kwargs)

    assert first.projection == second.projection
    assert first.report == second.report


def test_explicit_event_tag_parser_accepts_only_one_event() -> None:
    event_id = "decision-event:abc123"

    assert (
        extract_explicit_decision_event_id(
            f"smartcrypto_long|decision_event_id={event_id}"
        )
        == event_id
    )


def test_missing_decision_event_id_blocks_projection() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            enter_tag="smartcrypto_long",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.status == "blocked"
    assert outcome.report.reason == (
        "closed_paper_trade_decision_event_id_missing"
    )


def test_ambiguous_decision_event_id_blocks_projection() -> None:
    decision = _strict_decision()
    event_id = decision.decision_projection.target_payload.event_id
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            enter_tag=(
                "smartcrypto_long"
                f"|decision_event_id={event_id}"
                f"|decision_event_id={event_id}"
            ),
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_decision_event_id_ambiguous"
    )



def test_duplicate_different_decision_event_ids_are_ambiguous() -> None:
    decision = _strict_decision()
    event_id = decision.decision_projection.target_payload.event_id
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            enter_tag=(
                "smartcrypto_long"
                f"|decision_event_id={event_id}"
                "|decision_event_id=decision-event:other456"
            ),
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_decision_event_id_ambiguous"
    )


def test_empty_decision_event_id_token_is_invalid() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            enter_tag="smartcrypto_long|decision_event_id=",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_decision_event_id_invalid"
    )

def test_mismatched_decision_event_id_blocks_projection() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            enter_tag=(
                "smartcrypto_long|"
                "decision_event_id=decision-event:other123"
            ),
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_decision_event_id_mismatch"
    )


def test_open_trade_is_not_eligible() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision, is_open=1),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == "closed_paper_trade_required"


def test_non_positive_trade_id_is_rejected() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision, id=0),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_id_must_be_positive_integer"
    )


def test_pair_mismatch_fails_strict_trade_link_projection() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            pair="ETH/USDT:USDT",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == "strict_trade_link_projection_failed"


def test_side_mismatch_fails_strict_trade_link_projection() -> None:
    decision = _strict_decision(side="long")
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision, is_short=1),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == "strict_trade_link_projection_failed"


def test_trade_execution_before_decision_fails_projection() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            open_date="2026-08-21T18:00:00+00:00",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == "strict_trade_link_projection_failed"


def test_non_utc_trade_timestamp_is_rejected() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            open_date="2026-08-21T15:00:03-03:00",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_timestamp_not_utc:open_date"
    )


def test_post_execution_identity_override_is_forbidden() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(
            decision,
            candidate_id="candidate-from-trade-row",
        ),
        source_database_sha256="d" * 64,
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_identity_override_forbidden:candidate_id"
    )


def test_invalid_source_database_hash_is_rejected() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision),
        source_database_sha256="not-a-sha256",
    )

    assert outcome.projection is None
    assert outcome.report.reason == (
        "closed_paper_trade_sha256_invalid:source_database_sha256"
    )


def test_report_keeps_runtime_writer_and_execution_disabled() -> None:
    decision = _strict_decision()
    outcome = project_closed_paper_trade_link_readonly(
        decision=decision,
        trade_row=_trade_row(decision),
        source_database_sha256="d" * 64,
    )

    payload = outcome.report.model_dump()
    assert payload["read_only"] is True
    assert payload["writer_invoked"] is False
    assert payload["writes_runtime"] is False
    assert payload["writes_sqlite"] is False
    assert payload["reads_sqlite"] is False
    assert payload["runtime_integration_executed"] is False
    assert payload["changes_risk"] is False
    assert payload["changes_strategy"] is False
    assert payload["sends_orders"] is False
    assert payload["historical_backfill"] is False
    assert payload["timestamp_nearest_matching_allowed"] is False
    assert payload["symbol_side_candidate_inference_allowed"] is False


def test_trade_projection_module_has_no_io_writer_or_execution_imports() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))

    forbidden_prefixes = (
        "freqtrade",
        "ccxt",
        "redis",
        "sqlite3",
        "smartcrypto.execution.decision_ledger_paper_runtime_writer_v1",
        "smartcrypto.execution.signal_risk_gate",
        "smartcrypto.execution.signal_producer",
    )
    forbidden_calls = {
        "open",
        "write_text",
        "write_bytes",
        "create_paper_runtime_writer",
        "send_order",
        "submit_order",
    }

    imported: list[str] = []
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    for module in imported:
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in forbidden_prefixes
        )

    assert not (calls & forbidden_calls)
