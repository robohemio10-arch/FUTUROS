from smartcrypto.risk.risk_manager import RiskLimits, RiskManager


def test_approves_long_signal() -> None:
    manager = RiskManager(
        RiskLimits(
            runtime_mode="paper",
            max_position_usdt=50,
            max_leverage=2,
            min_score_long=0.6,
            max_score_short=0.4,
            signal_ttl_seconds=300,
            kill_switch_enabled=False,
            allowed_pairs=("BTC/USDT:USDT",),
        )
    )

    decision = manager.approve({"pair": "BTC/USDT:USDT", "score": 0.7})

    assert decision.approved
    assert decision.signal["side"] == "long"
