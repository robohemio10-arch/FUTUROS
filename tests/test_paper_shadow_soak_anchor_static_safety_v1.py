from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED_PATHS = (
    ROOT / "smartcrypto" / "ops" / "paper_shadow_soak_anchor",
    ROOT / "scripts" / "audit_paper_shadow_soak_anchor_continuity_pack.py",
)
FORBIDDEN_PATTERNS = (
    "import ccxt",
    "ccxt.",
    "create_order(",
    "cancel_order(",
    "fetch_balance(",
    "fetch_open_orders(",
    "OrderManager(",
    "ExchangeGateway(",
    "CommandBus(",
    "NotificationDispatcher(",
    "requests.post(",
    "httpx.post(",
    "aiohttp.",
    "asyncio.create_task(",
    "TELEGRAM_TOKEN",
    "NTFY_TOKEN",
    "BINANCE_SECRET",
    "BINANCE_API_KEY",
    "yaml.dump(",
    "yaml.safe_dump(",
    "run_ocr",
    "import_trades",
    "rebuild_training_dataset",
    "clean_ai_shadow",
    "promote_model",
    "enable_live",
    "enable_canary",
)


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in SCANNED_PATHS:
        if path.is_file():
            files.append(path)
        else:
            files.extend(sorted(child for child in path.rglob("*.py") if child.is_file()))
    return files


def test_soak_anchor_branch_has_no_operational_side_effect_imports() -> None:
    offenders: list[str] = []
    for path in iter_files():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in text:
                offenders.append(f"{path.relative_to(ROOT)}:{pattern}")
    assert offenders == []


def test_no_runtime_or_binary_test_fixtures_were_added() -> None:
    forbidden_suffixes = {".parquet", ".sqlite", ".sqlite3", ".db", ".csv", ".xlsx", ".jsonl", ".zip"}
    added_test_fixtures = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    ]
    assert added_test_fixtures == []
