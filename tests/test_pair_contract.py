from smartcrypto.execution.freqtrade_contract import ccxt_symbol, freqtrade_pair, internal_symbol


def test_pair_contract() -> None:
    assert internal_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert freqtrade_pair("BTCUSDT") == "BTC/USDT:USDT"
