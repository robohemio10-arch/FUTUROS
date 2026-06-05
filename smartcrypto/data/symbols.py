from __future__ import annotations

_SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "BTCUSDT": "BTCUSDT",
    "BTC/USDT": "BTCUSDT",
    "BTC/USDT:USDT": "BTCUSDT",
    "ETH": "ETHUSDT",
    "ETHUSDT": "ETHUSDT",
    "ETH/USDT": "ETHUSDT",
    "ETH/USDT:USDT": "ETHUSDT",
}


def normalize_symbol(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = text.replace(" ", "")
    text = text.replace("-", "/")
    return _SYMBOL_MAP.get(text, text.replace("/", "").replace(":USDT", ""))


def to_ccxt_pair(symbol: object) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}/USDT"
    return str(symbol)


def to_freqtrade_pair(symbol: object) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}/USDT:USDT"
    return str(symbol)
