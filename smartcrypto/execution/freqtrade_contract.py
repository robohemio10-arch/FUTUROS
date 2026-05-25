from __future__ import annotations


def internal_symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    normalized = normalized.replace(":", "")
    normalized = normalized.replace("/", "")
    normalized = normalized.replace("-", "")
    normalized = normalized.replace("_", "")
    normalized = normalized.replace("PERP", "")

    if normalized.endswith("USDTUSDT"):
        normalized = normalized[:-4]

    if normalized in {"BTC", "ETH", "BNB", "SOL", "XRP"}:
        normalized = f"{normalized}USDT"

    return normalized


def ccxt_symbol(value: str) -> str:
    symbol = internal_symbol(value)
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol


def freqtrade_pair(value: str) -> str:
    symbol = internal_symbol(value)
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    return symbol
