from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml
import pytest

from smartcrypto.settings import RuntimeSettings


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.paper.yml"
SETTINGS_PATH = ROOT / "smartcrypto/settings.py"
HEALTHCHECK_PATH = ROOT / "smartcrypto/runtime/qlib_refresh_supervisor_healthcheck.py"
LEDGER_CONFIG_PATH = ROOT / "config/decision_ledger_paper_observability.yml"
CANONICAL_CONTAINER_PATH = "/app/data/predictions/latest_qlib_predictions.parquet"
CANONICAL_RELATIVE_PATH = "data/predictions/latest_qlib_predictions.parquet"
LEGACY_NAME = "latest_predictions.parquet"


def services() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return payload["services"]


def test_bot_uses_exact_canonical_qlib_predictions_path() -> None:
    bot = services()["smartcrypto-bot-paper"]

    assert bot["environment"]["SMARTCRYPTO_PREDICTIONS_PATH"] == CANONICAL_CONTAINER_PATH


def test_runtime_settings_default_uses_canonical_qlib_predictions_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMARTCRYPTO_PREDICTIONS_PATH", raising=False)

    settings = RuntimeSettings.from_env()

    assert settings.predictions_path == Path(CANONICAL_RELATIVE_PATH)


def test_qlib_supervisor_has_specific_current_instance_healthcheck() -> None:
    health = services()["qlib-refresh-supervisor-paper"]["healthcheck"]
    command = [str(item) for item in health["test"]]

    assert command == [
        "CMD",
        "python",
        "-m",
        "smartcrypto.runtime.qlib_refresh_supervisor_healthcheck",
        "--quiet",
        "--report",
        "/app/data/reports/qlib_paper_refresh_supervisor_report.json",
        "--predictions",
        CANONICAL_CONTAINER_PATH,
        "--market-features",
        "/app/data/features/market_features_60d.parquet",
        "--max-age-seconds",
        "420",
    ]
    assert health == {
        "test": command,
        "interval": "15s",
        "timeout": "10s",
        "start_period": "120s",
        "retries": 5,
    }


def test_bot_waits_only_for_healthy_qlib_supervisor() -> None:
    bot = services()["smartcrypto-bot-paper"]

    assert bot["depends_on"] == {
        "qlib-refresh-supervisor-paper": {"condition": "service_healthy"}
    }
    assert "user" not in bot
    assert "trade-event-notifications-paper" not in bot["depends_on"]
    assert "paper-autolearning-scheduler" not in bot["depends_on"]


def test_optional_services_remain_profile_isolated() -> None:
    payload = services()

    assert payload["trade-event-notifications-paper"]["profiles"] == ["notifications"]
    assert payload["paper-autolearning-scheduler"]["profiles"] == ["autolearning"]


def test_core_services_preserve_paper_only_environment() -> None:
    payload = services()
    for service_name in ("qlib-refresh-supervisor-paper", "smartcrypto-bot-paper"):
        environment = payload[service_name]["environment"]
        assert environment["SMARTCRYPTO_RUNTIME_MODE"] == "paper"
        assert environment["LIVE_ENABLED"] == "false"
        assert environment["ORDER_SUBMISSION_ENABLED"] == "false"
        assert environment["REAL_ORDER_SUBMISSION_ENABLED"] == "false"
        assert environment["SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS"] == "false"


def test_decision_ledger_remains_disabled() -> None:
    payload = yaml.safe_load(LEDGER_CONFIG_PATH.read_text(encoding="utf-8"))

    assert payload["enabled"] is False
    assert payload["writer_enabled"] is False
    assert payload["trade_link_enabled"] is False
    assert payload["writer_profile"]["activation_state"] == "disabled"
    assert payload["writer_profile"]["enabled"] is False
    assert payload["writer_profile"]["runtime_write_authorized"] is False


def test_legacy_prediction_name_is_absent_from_core_paper_contract() -> None:
    source = "\n".join(
        (
            COMPOSE_PATH.read_text(encoding="utf-8"),
            SETTINGS_PATH.read_text(encoding="utf-8"),
            HEALTHCHECK_PATH.read_text(encoding="utf-8"),
        )
    )

    assert LEGACY_NAME not in source


def test_healthcheck_is_static_read_only_and_has_no_operational_imports() -> None:
    source = HEALTHCHECK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert imported.isdisjoint(
        {
            "ccxt",
            "freqtrade",
            "requests",
            "subprocess",
            "smartcrypto.risk.risk_manager",
            "smartcrypto.qlib_engine.fresh_prediction_runner",
        }
    )
    for token in (
        "write_text(",
        "write_bytes(",
        "to_parquet(",
        "urlopen(",
        "create_order(",
        "fetch_balance(",
    ):
        assert token not in source
