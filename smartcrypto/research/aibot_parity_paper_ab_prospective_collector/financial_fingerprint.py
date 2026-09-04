"""Deterministic financial-config fingerprint for the Paper A/B cohort.

The fingerprint includes economic/trading-policy fields, selected strategy
semantics, and a canonical LF hash of the configured strategy source so any
strategy behavior change invalidates the cohort baseline. Credential-bearing
fields are never projected into the canonical payload. Raw source hashes are
retained separately as audit metadata.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from smartcrypto.research.aibot_parity_orchestrator.contracts import canonical_sha256

FINGERPRINT_SCHEMA_VERSION = "aibot_parity_paper_financial_config_fingerprint_v1"
DEFAULT_PAPER_CONFIG = Path("freqtrade/user_data/config.paper.json")
DEFAULT_STRATEGY_FILE = Path(
    "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
)
DEFAULT_STRATEGY_NAME = "SmartCryptoSignalStrategy"

_CONFIG_KEYS = (
    "dry_run",
    "dry_run_wallet",
    "cancel_open_orders_on_exit",
    "trading_mode",
    "margin_mode",
    "liquidation_buffer",
    "futures_funding_rate",
    "stake_currency",
    "stake_amount",
    "tradable_balance_ratio",
    "max_open_trades",
    "timeframe",
    "process_only_new_candles",
    "minimal_roi",
    "stoploss",
    "use_exit_signal",
    "exit_profit_only",
    "exit_profit_offset",
    "ignore_roi_if_entry_signal",
    "ignore_buying_expired_candle_after",
    "position_adjustment_enable",
    "force_entry_enable",
    "order_types",
    "order_time_in_force",
    "unfilledtimeout",
    "entry_pricing",
    "exit_pricing",
    "pairlists",
)
_EXCHANGE_KEYS = ("name", "pair_whitelist", "pair_blacklist")
_STRATEGY_CLASS_KEYS = (
    "timeframe",
    "can_short",
    "minimal_roi",
    "stoploss",
    "process_only_new_candles",
    "startup_candle_count",
    "use_exit_signal",
    "exit_profit_only",
    "ignore_roi_if_entry_signal",
    "trailing_stop",
)


@dataclass(frozen=True)
class FinancialConfigFingerprint:
    schema_version: str
    paper_financial_config_sha256: str
    canonical_payload: dict[str, Any]
    source_paths: dict[str, str]
    source_sha256: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "paper_financial_config_sha256": self.paper_financial_config_sha256,
            "canonical_payload": self.canonical_payload,
            "source_paths": self.source_paths,
            "source_sha256": self.source_sha256,
            "secrets_projected": False,
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("paper_config_payload_must_be_object")
    return dict(payload)


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError) as exc:
        raise ValueError("strategy_financial_literal_not_static") from exc


def _strategy_financial_projection(path: Path, strategy_name: str) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    strategy_class = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == strategy_name
        ),
        None,
    )
    if strategy_class is None:
        raise ValueError(f"strategy_class_not_found:{strategy_name}")

    class_values: dict[str, Any] = {}
    leverage_source_sha256_lf: str | None = None
    for node in strategy_class.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target: ast.expr | None
            value_node: ast.AST | None
            if isinstance(node, ast.Assign):
                target = node.targets[0] if len(node.targets) == 1 else None
                value_node = node.value
            else:
                target = node.target
                value_node = node.value
            if (
                isinstance(target, ast.Name)
                and target.id in _STRATEGY_CLASS_KEYS
                and value_node is not None
            ):
                class_values[target.id] = _literal(value_node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "leverage":
            leverage_source = ast.get_source_segment(source, node)
            if leverage_source is None:
                raise ValueError("strategy_leverage_source_unavailable")
            normalized_leverage = leverage_source.replace("\r\n", "\n").replace("\r", "\n")
            leverage_source_sha256_lf = hashlib.sha256(
                normalized_leverage.encode("utf-8")
            ).hexdigest()

    missing = sorted(set(_STRATEGY_CLASS_KEYS) - set(class_values))
    if missing:
        raise ValueError("strategy_financial_fields_missing:" + ",".join(missing))
    if leverage_source_sha256_lf is None:
        raise ValueError("strategy_leverage_method_missing")

    return {
        "strategy_name": strategy_name,
        "class_financial_values": class_values,
        "leverage_source_sha256_lf": leverage_source_sha256_lf,
    }


def _config_financial_projection(config: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(key for key in _CONFIG_KEYS if key not in config)
    if missing:
        raise ValueError("paper_config_financial_fields_missing:" + ",".join(missing))
    exchange = config.get("exchange")
    if not isinstance(exchange, Mapping):
        raise ValueError("paper_config_exchange_must_be_object")
    missing_exchange = sorted(key for key in _EXCHANGE_KEYS if key not in exchange)
    if missing_exchange:
        raise ValueError("paper_config_exchange_fields_missing:" + ",".join(missing_exchange))

    return {
        "config": {key: config[key] for key in _CONFIG_KEYS},
        "exchange": {key: exchange[key] for key in _EXCHANGE_KEYS},
    }


def build_paper_financial_config_fingerprint(
    *,
    project_root: str | Path,
    paper_config_path: str | Path = DEFAULT_PAPER_CONFIG,
    strategy_path: str | Path = DEFAULT_STRATEGY_FILE,
    strategy_name: str = DEFAULT_STRATEGY_NAME,
) -> FinancialConfigFingerprint:
    """Build a deterministic cohort fingerprint without exposing credentials."""

    root = Path(project_root).resolve()
    config_path = Path(paper_config_path)
    config_path = (
        config_path.resolve()
        if config_path.is_absolute()
        else (root / config_path).resolve()
    )
    strategy_file = Path(strategy_path)
    strategy_file = (
        strategy_file.resolve()
        if strategy_file.is_absolute()
        else (root / strategy_file).resolve()
    )
    if not config_path.is_file():
        raise FileNotFoundError(f"paper_config_not_found:{config_path}")
    if not strategy_file.is_file():
        raise FileNotFoundError(f"paper_strategy_not_found:{strategy_file}")

    config = _load_json_object(config_path)
    canonical_payload = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "freqtrade": _config_financial_projection(config),
        "strategy": {
            **_strategy_financial_projection(strategy_file, strategy_name),
            "strategy_source_sha256_lf": _canonical_text_sha256(strategy_file),
        },
    }
    fingerprint = canonical_sha256(canonical_payload)
    return FinancialConfigFingerprint(
        schema_version=FINGERPRINT_SCHEMA_VERSION,
        paper_financial_config_sha256=fingerprint,
        canonical_payload=canonical_payload,
        source_paths={
            "paper_config": str(config_path),
            "strategy": str(strategy_file),
        },
        source_sha256={
            "paper_config": _sha256_file(config_path),
            "strategy": _sha256_file(strategy_file),
        },
    )


__all__ = [
    "DEFAULT_PAPER_CONFIG",
    "DEFAULT_STRATEGY_FILE",
    "DEFAULT_STRATEGY_NAME",
    "FINGERPRINT_SCHEMA_VERSION",
    "FinancialConfigFingerprint",
    "build_paper_financial_config_fingerprint",
]
