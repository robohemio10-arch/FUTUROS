from __future__ import annotations

import importlib.util
import warnings
from pathlib import Path

import pandas as pd


MODULE_PATH = Path("scripts/build_trade_enriched.py")


def load_module():
    spec = importlib.util.spec_from_file_location("build_trade_enriched", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_without_warnings(values):
    module = load_module()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        parsed = module.parse_trade_timestamp_series(pd.Series(values))
    parsing_warnings = [
        item
        for item in captured
        if "Parsing dates" in str(item.message) or "dayfirst" in str(item.message)
    ]
    assert parsing_warnings == []
    return parsed


def test_parse_iso_with_milliseconds_without_warning() -> None:
    parsed = parse_without_warnings(["2026-05-23 20:37:10.797383"])

    assert parsed.iloc[0] == pd.Timestamp("2026-05-23T20:37:10.797383Z")


def test_parse_iso_without_milliseconds_without_warning() -> None:
    parsed = parse_without_warnings(["2026-05-23 20:37:10"])

    assert parsed.iloc[0] == pd.Timestamp("2026-05-23T20:37:10Z")


def test_parse_iso_with_t_separator_without_warning() -> None:
    parsed = parse_without_warnings(["2026-05-23T20:37:10.123"])

    assert parsed.iloc[0] == pd.Timestamp("2026-05-23T20:37:10.123Z")


def test_parse_brazilian_slash_dayfirst() -> None:
    parsed = parse_without_warnings(["23/05/2026 20:37:10"])

    assert parsed.iloc[0] == pd.Timestamp("2026-05-23T20:37:10Z")


def test_parse_brazilian_dash_dayfirst() -> None:
    parsed = parse_without_warnings(["23-05-2026 20:37:10"])

    assert parsed.iloc[0] == pd.Timestamp("2026-05-23T20:37:10Z")


def test_invalid_values_become_nat_without_exception() -> None:
    parsed = parse_without_warnings(["not-a-date", "", None])

    assert parsed.isna().tolist() == [True, True, True]


def test_mixed_iso_and_brazilian_series_keeps_utc() -> None:
    parsed = parse_without_warnings(
        [
            "2026-05-23 20:37:10.797383",
            "23/05/2026 20:37:10",
            "2026-05-23T20:37:10",
            "23-05-2026 20:37:10",
        ]
    )

    assert str(parsed.dtype) == "datetime64[ns, UTC]"
    assert parsed.notna().all()
    assert parsed.dt.tz is not None
    assert all(value == pd.Timestamp("2026-05-23T20:37:10Z") for value in parsed.iloc[1:])


def test_module_does_not_reference_exchange_or_live_flags() -> None:
    text = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
    ]
    assert all(token not in text for token in forbidden)
