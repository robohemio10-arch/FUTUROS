from __future__ import annotations

from smartcrypto.dashboard.ui import (
    normalize_status,
    render_compact_metric_card,
    render_empty_state,
    render_error_state,
    render_footer_audit_bar,
    render_html_table,
    render_metric_card,
    render_status_badges,
    render_status_card,
    render_unknown_state,
)


def test_cards_return_safe_html() -> None:
    metric = render_metric_card("PnL <script>", "<b>10</b>", unit="USDT", helper="safe")
    assert "sfc-card" in metric
    assert "<script>" not in metric
    assert "&lt;script&gt;" in metric
    assert "&lt;b&gt;10&lt;/b&gt;" in metric
    assert "sfc-card" in render_status_card("Runtime", "OK", "healthy")
    assert "sfc-card-sm" in render_compact_metric_card("Latency", "12 ms", "WARNING")


def test_global_badges_and_footer_expose_permanent_safety_posture() -> None:
    badges = render_status_badges()
    for label in (
        "PAPER / SHADOW ONLY", "LIVE LOCKED", "ORDER SUBMISSION DISABLED",
        "READINESS BLOCKED", "RISKMANAGER AUTHORITY",
    ):
        assert label in badges
    footer = render_footer_audit_bar("snapshot.json")
    for label in (
        "Dashboard Read-only", "Sem ccxt", "Sem create_order", "Sem OrderManager direto",
        "Sem live trading",
    ):
        assert label in footer


def test_html_table_handles_rows_empty_values_statuses_and_escaping() -> None:
    table = render_html_table(
        [{"Name": "<unsafe>", "Status": "OK"}],
        status_columns=["Status"],
    )
    assert "<table" in table
    assert "&lt;unsafe&gt;" in table
    assert "sfc-status-ok" in table
    assert "Sem dados disponíveis" in render_html_table([])


def test_status_normalization_and_states() -> None:
    assert normalize_status("PASS") == "ok"
    assert normalize_status("WARN") == "warning"
    assert normalize_status("BLOCKED") == "blocked"
    assert normalize_status("HARD-BLOCKED") == "hard_blocked"
    assert normalize_status("MISSING_OPTIONAL") == "unknown"
    assert "sfc-state-empty" in render_empty_state()
    assert "sfc-state-unknown" in render_unknown_state()
    assert "sfc-state-error" in render_error_state(details="detail")
