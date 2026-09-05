from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from smartcrypto.learning.paper_autolearning.economic_oos_validation import (
    build_paper_autolearning_economic_oos_validation_v1,
)


def make_rows(*, selected_oos_pnl: float = 2.0, selected_every: int = 1) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[dict[str, object]] = []
    for index in range(240):
        is_oos = index >= 168
        selected = is_oos and (index % selected_every == 0)
        pnl = selected_oos_pnl if selected else (-0.2 if index % 2 else 0.1)
        rows.append(
            {
                "is_closed": True,
                "close_time_utc": (start + timedelta(minutes=5 * index)).isoformat(),
                "net_pnl": pnl,
                "paper_candidate_filter_decision": "allowed" if selected else "blocked",
            }
        )
    return rows


def test_oos_edge_passes_with_profitable_selected_trades(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=tmp_path,
        rows=make_rows(),
    )

    assert report["status"] == "ok"
    assert report["decision"] == "OOS_ECONOMICALLY_PROMISING_RESEARCH_ONLY"
    assert report["oos_trade_count"] == 72
    assert report["oos_selected_trade_count"] == 72
    assert report["chronological_boundary_valid"] is True
    assert report["oos_selected_metrics"]["net_pnl_total"] > 0
    assert report["oos_selected_metrics"]["profit_factor"] == "inf"


def test_oos_requires_enough_selected_trades(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=tmp_path,
        rows=make_rows(selected_every=2),
    )

    assert report["status"] == "blocked"
    assert "min_oos_selected_trades_not_met" in report["blockers"]
    assert report["decision"] == "MANTER_EM_RESEARCH"


def test_oos_rejects_negative_selected_economics(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=tmp_path,
        rows=make_rows(selected_oos_pnl=-1.0),
    )

    assert report["status"] == "blocked"
    assert "oos_positive_net_pnl_not_met" in report["blockers"]
    assert "oos_positive_expectancy_not_met" in report["blockers"]


def test_oos_fails_closed_on_invalid_time(tmp_path: Path) -> None:
    rows = make_rows()
    rows[10]["close_time_utc"] = "not-a-time"

    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=tmp_path,
        rows=rows,
    )

    assert report["status"] == "blocked"
    assert "unparseable_close_time_detected" in report["blockers"]
    assert report["anti_leakage"] is True


def test_oos_preserves_operational_safety(tmp_path: Path) -> None:
    report = build_paper_autolearning_economic_oos_validation_v1(
        project_root=tmp_path,
        rows=make_rows(),
    )

    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["research_only"] is True
    assert report["operational_authority"] is False
    assert report["promotion_allowed"] is False
    assert report["model_promotion_performed"] is False
    assert report["active_model_changed"] is False
    assert report["updates_qlib_runtime"] is False
    assert report["updates_ai_shadow_runtime"] is False
    assert report["changes_risk"] is False
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False
    assert report["writes_runtime"] is False
    assert report["write_performed"] is False
