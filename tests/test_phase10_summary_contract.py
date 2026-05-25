from smartcrypto.ops.phase10_summary import build_phase10_summary


def test_phase10_summary_has_status_contract():
    summary = build_phase10_summary()
    assert "phase10_status" in summary
    assert "signals" in summary
    assert "decision_log" in summary
    assert "freqtrade_trades" in summary
