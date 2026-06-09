from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.live_canary_contract import build_live_canary_contract_with_hard_blocks


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def seed_manual_go(root: Path) -> None:
    write_json(
        root,
        "data/reports/manual_go_no_go_live_canary_governance.json",
        {
            "status": "manual_go_recorded",
            "manual_decision": "GO",
            "manual_decision_status": "valid",
            "manual_go_no_go_required": True,
            "release_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "auto_promotion_allowed": False,
            "changes_risk": False,
            "sends_orders": False,
        },
    )


def seed_valid_candidate(root: Path) -> Path:
    return write_json(
        root,
        "data/governance/candidate_canary_config.json",
        {
            "global_cap_usdt": 30,
            "per_symbol_cap_usdt": 10,
            "allowed_symbols": ["BTC/USDT", "ETH/USDT"],
            "max_safety_orders": 0,
            "martingale_multiplier": 1.0,
            "preferred_order_type": "LIMIT_MAKER",
            "manual_go_no_go_required": True,
            "hard_blocks_enforced": True,
            "kill_switch_required": True,
            "reconciliation_required": True,
            "rollback_required": True,
            "observability_required": True,
            "paper_shadow_evidence_required": True,
            "auto_promotion_allowed": False,
            "market_buy_allowed": False,
            "market_order_allowed": False,
            "martingale_allowed": False,
            "safety_orders_allowed": False,
            "unbounded_capital_allowed": False,
            "private_exchange_access_allowed": False,
            "order_submission_allowed": False,
            "release_allowed": False,
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "changes_risk": False,
            "sends_orders": False,
        },
    )


def test_contract_missing_manual_governance_blocks(tmp_path: Path) -> None:
    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert result.report["release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert "manual_governance_missing" in result.report["blocking_reasons"]


def test_contract_with_manual_go_and_no_candidate_is_warning_only(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)

    result = build_live_canary_contract_with_hard_blocks(
        project_root=tmp_path,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["status"] == "contract_defined_with_warnings"
    assert "candidate_config_not_supplied_contract_only" in result.report["warning_reasons"]
    assert result.report["release_allowed"] is False
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False
    assert result.report["sends_orders"] is False
    assert result.report["changes_risk"] is False


def test_valid_candidate_defines_contract_but_never_releases(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)

    result = build_live_canary_contract_with_hard_blocks(
        project_root=tmp_path,
        candidate_config_path=candidate,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["status"] == "contract_defined"
    assert result.report["blocking_reasons"] == []
    assert result.report["contract"]["global_cap_min_usdt"] == 20.0
    assert result.report["contract"]["global_cap_max_usdt"] == 50.0
    assert result.report["contract"]["per_symbol_cap_usdt"] == 10.0
    assert result.report["release_allowed"] is False
    assert result.report["auto_promotion_allowed"] is False


def test_no_go_manual_governance_blocks(tmp_path: Path) -> None:
    write_json(
        tmp_path,
        "data/reports/manual_go_no_go_live_canary_governance.json",
        {"status": "blocked", "manual_decision": "NO_GO", "manual_go_no_go_required": True},
    )

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("manual_governance" in reason for reason in result.report["blocking_reasons"])


def test_candidate_above_global_cap_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["global_cap_usdt"] = 100
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_global_cap_above_maximum" in result.report["blocking_reasons"]


def test_candidate_below_global_cap_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["global_cap_usdt"] = 10
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_global_cap_below_minimum" in result.report["blocking_reasons"]


def test_candidate_above_per_symbol_cap_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["per_symbol_cap_usdt"] = 11
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_per_symbol_cap_above_maximum" in result.report["blocking_reasons"]


def test_candidate_disallowed_symbol_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["allowed_symbols"] = ["BTC/USDT", "SOL/USDT"]
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("candidate_symbols_not_allowed" in reason for reason in result.report["blocking_reasons"])


def test_candidate_safety_orders_block(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["max_safety_orders"] = 1
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_max_safety_orders_must_be_zero" in result.report["blocking_reasons"]


def test_candidate_martingale_multiplier_block(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["martingale_multiplier"] = 1.2
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_martingale_multiplier_must_be_one" in result.report["blocking_reasons"]


def test_candidate_market_buy_block(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["market_buy_allowed"] = True
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("market_buy_allowed" in reason for reason in result.report["blocking_reasons"])


def test_candidate_wrong_order_type_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["preferred_order_type"] = "MARKET"
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_preferred_order_type_not_limit_maker" in result.report["blocking_reasons"]


def test_missing_required_hard_block_true_value_blocks(tmp_path: Path) -> None:
    seed_manual_go(tmp_path)
    candidate = seed_valid_candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["kill_switch_required"] = False
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, candidate_config_path=candidate, no_write=True)

    assert result.report["status"] == "blocked"
    assert "candidate_kill_switch_required_must_be_true" in result.report["blocking_reasons"]


def test_write_enabled_creates_report(tmp_path: Path) -> None:
    result = build_live_canary_contract_with_hard_blocks(project_root=tmp_path, no_write=False)

    assert result.write_performed is True
    assert result.output_path.exists()
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "live_canary_contract_with_hard_blocks_v1"
    assert payload["release_allowed"] is False


def test_generated_at_is_stable(tmp_path: Path) -> None:
    result = build_live_canary_contract_with_hard_blocks(
        project_root=tmp_path,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
