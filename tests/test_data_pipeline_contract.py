from smartcrypto.execution.freqtrade_contract import ccxt_symbol, freqtrade_pair, internal_symbol


def test_symbol_contracts() -> None:
    assert internal_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert internal_symbol("ethusdt") == "ETHUSDT"
    assert ccxt_symbol("BTCUSDT") == "BTC/USDT"
    assert freqtrade_pair("ETHUSDT") == "ETH/USDT:USDT"
