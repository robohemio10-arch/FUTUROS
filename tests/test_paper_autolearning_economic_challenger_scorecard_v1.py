from __future__ import annotations

from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_challenger_scorecard import (
    build_paper_autolearning_economic_challenger_scorecard_v1,
)


def row(index: int, pnl: float, selected: bool) -> dict[str, object]:
    return {
        "event_id": f"e{index}",
        "is_closed": True,
        "close_time_utc": f"2026-09-{(index % 28) + 1:02d}T12:00:00Z",
        "net_pnl": pnl,
        "paper_candidate_filter_decision": "allow" if selected else "block",
    }


def test_profitable_selected_subset_passes_research_only_gate(tmp_path: Path) -> None:
    rows = []
    for i in range(120):
        rows.append(row(i, 2.0 if i % 4 != 0 else -1.0, True))
    for i in range(120, 240):
        rows.append(row(i, 0.4 if i % 2 == 0 else -1.0, False))

    report = build_paper_autolearning_economic_challenger_scorecard_v1(
        project_root=tmp_path,
        rows=rows,
    )

    assert report["status"] == "ok"
    assert report["decision"] == "ECONOMICALLY_PROMISING_RESEARCH_ONLY"
    assert report["selected_trade_count"] == 120
    assert report["selected_metrics"]["profit_factor"] > 1.1
    assert report["selected_metrics"]["expectancy"] > report["baseline_metrics"]["expectancy"]
    assert report["promotion_allowed"] is False
    assert report["sends_orders"] is False


def test_insufficient_sample_is_blocked(tmp_path: Path) -> None:
    rows = [row(i, 2.0, True) for i in range(50)]
    report = build_paper_autolearning_economic_challenger_scorecard_v1(project_root=tmp_path, rows=rows)

    assert report["status"] == "blocked"
    assert "min_selected_trades_not_met" in report["blockers"]
    assert report["decision"] == "MANTER_EM_RESEARCH"


def test_negative_expectancy_is_blocked(tmp_path: Path) -> None:
    rows = [row(i, -1.0 if i % 2 == 0 else 0.2, True) for i in range(120)]
    report = build_paper_autolearning_economic_challenger_scorecard_v1(project_root=tmp_path, rows=rows)

    assert report["status"] == "blocked"
    assert "positive_expectancy_not_met" in report["blockers"]
    assert "positive_net_pnl_not_met" in report["blockers"]


def test_missing_selector_field_fails_closed(tmp_path: Path) -> None:
    rows = [{"is_closed": True, "net_pnl": 1.0, "close_time_utc": "2026-09-01T00:00:00Z"} for _ in range(120)]
    report = build_paper_autolearning_economic_challenger_scorecard_v1(project_root=tmp_path, rows=rows)

    assert report["status"] == "blocked"
    assert "missing_selector_field:paper_candidate_filter_decision" in report["blockers"]


def test_never_grants_operational_authority(tmp_path: Path) -> None:
    rows = [row(i, 1.0, True) for i in range(120)]
    report = build_paper_autolearning_economic_challenger_scorecard_v1(project_root=tmp_path, rows=rows)

    assert report["operational_authority"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["changes_risk"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
