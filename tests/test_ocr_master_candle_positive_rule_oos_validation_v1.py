from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from smartcrypto.research.ocr_master_candle_positive_rule_oos_validation.oos_validation import (
    build_positive_rule_oos_validation_report,
    discover_positive_candidates,
    validate_candidates_oos,
)


def _aligned_fixture() -> pd.DataFrame:
    rows = []
    # Six chronological months.  The long/hour=1 slice is stable and positive.
    for month in range(1, 7):
        for index in range(6):
            rows.append(
                {
                    "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                    "side_norm": "long",
                    "hour": "1",
                    "duration_bucket": "le_30m",
                    "regime_bucket": "pullback_down",
                    "day": f"2026-{month:02d}-{index + 1:02d}",
                    "open_time_utc": pd.Timestamp(datetime(2026, month, index + 1, 1, 0, tzinfo=UTC)),
                    "pnl_usdt": 3.0,
                    "entry_price": 100.0,
                }
            )
        for index in range(10):
            rows.append(
                {
                    "symbol_norm": "BTCUSDT" if index % 2 == 0 else "ETHUSDT",
                    "side_norm": "short" if index % 2 == 0 else "long",
                    "hour": str(8 + (index % 4)),
                    "duration_bucket": "le_30m",
                    "regime_bucket": "flat" if index % 2 == 0 else "bearish_short",
                    "day": f"2026-{month:02d}-{index + 10:02d}",
                    "open_time_utc": pd.Timestamp(datetime(2026, month, min(index + 10, 28), 8 + (index % 4), 0, tzinfo=UTC)),
                    "pnl_usdt": -1.2 if index % 2 == 0 else 0.3,
                    "entry_price": 100.0,
                }
            )
    frame = pd.DataFrame(rows)
    frame["is_winner"] = frame["pnl_usdt"] > 0
    frame["is_loser"] = frame["pnl_usdt"] <= 0
    return frame


def test_no_runtime_report_is_blocked_and_safe() -> None:
    report = build_positive_rule_oos_validation_report(project_root=".", no_write=True)

    assert report["status"] == "blocked"
    assert report["input_mode"] == "no_runtime_rows_loaded"
    assert report["allow_runtime_read"] is False
    assert report["aligned_rows"] == 0
    assert report["oos_evaluated_candidate_count"] == 0
    assert report["operational_authority"] is False
    assert report["paper_observation_allowed"] is False
    assert report["ready_for_candidate_registry"] is False
    assert report["sends_orders"] is False


def test_discover_positive_candidates_from_aligned_fixture() -> None:
    baseline, candidates, positive = discover_positive_candidates(
        _aligned_fixture(),
        min_trade_count=6,
        max_day_concentration=0.35,
    )

    assert baseline["trade_count"] > 0
    assert len(candidates) > 0
    assert any(candidate["candidate_id"] == "include__side_norm_long__hour_1" for candidate in positive)
    assert all(candidate["ready_for_candidate_registry"] is False for candidate in positive)


def test_oos_validation_creates_monthly_walk_forward_shortlist() -> None:
    result = validate_candidates_oos(
        _aligned_fixture(),
        min_trade_count=6,
        max_day_concentration=0.35,
        min_oos_trade_count=2,
        min_oos_pass_ratio=0.60,
        min_oos_folds=3,
    )

    assert result["positive_candidate_count"] > 0
    assert result["oos_evaluated_candidate_count"] > 0
    assert len(result["folds"]) >= 5
    assert result["oos_surviving_candidate_count"] > 0
    survivor = result["oos_shortlist"][0]
    assert survivor["survives_oos_research_gate"] is True
    assert survivor["ready_for_candidate_registry"] is False
    assert survivor["paper_observation_allowed"] is False


def test_oos_validation_respects_strict_min_oos_folds() -> None:
    result = validate_candidates_oos(
        _aligned_fixture(),
        min_trade_count=6,
        max_day_concentration=0.35,
        min_oos_trade_count=2,
        min_oos_pass_ratio=0.60,
        min_oos_folds=99,
    )

    assert result["oos_evaluated_candidate_count"] > 0
    assert result["oos_surviving_candidate_count"] == 0
    assert any(
        "insufficient_oos_folds" in candidate["oos_rejection_reasons"]
        for candidate in result["oos_candidate_results"]
    )


def test_build_report_without_runtime_never_writes_even_if_write_requested(tmp_path: Path) -> None:
    report = build_positive_rule_oos_validation_report(
        project_root=tmp_path,
        write=True,
        no_write=True,
    )

    assert report["write_requested"] is False
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()


def test_report_gate_matrix_keeps_registry_and_paper_blocked() -> None:
    report = build_positive_rule_oos_validation_report(project_root=".", no_write=True)

    gates = {gate["gate_id"]: gate for gate in report["gate_matrix"]}
    assert gates["candidate_registry_blocked"]["passed"] is True
    assert gates["paper_observation_blocked"]["passed"] is True
    assert gates["promotion_blocked"]["passed"] is True
    assert report["can_promote_rules"] is False
    assert report["can_promote_model"] is False


def test_cli_no_runtime_json_executes() -> None:
    script = Path("scripts/build_ocr_master_candle_positive_rule_oos_validation_v1.py")
    completed = subprocess.run(
        [sys.executable, str(script), "--project-root", ".", "--no-write", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "ocr_master_candle_positive_rule_oos_validation_v1"
    assert payload["status"] == "blocked"
    assert payload["input_mode"] == "no_runtime_rows_loaded"


def test_oos_candidate_results_are_json_serializable() -> None:
    result = validate_candidates_oos(
        _aligned_fixture(),
        min_trade_count=6,
        max_day_concentration=0.35,
        min_oos_trade_count=2,
        min_oos_pass_ratio=0.60,
        min_oos_folds=3,
    )
    json.dumps(result, sort_keys=True, ensure_ascii=False)
