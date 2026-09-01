"""Typed research-only contracts for the AIBOT Trader Master benchmark."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


SOURCE_INVESTMENT_ID = "AIBOT_INVESTMENT_001"
SOURCE_REGISTRY_SCHEMA_VERSION = "aibot_source_registry_v1"
TRADE_SCHEMA_VERSION = "aibot_trader_master_trade_v1"
LOADER_VERSION = "aibot_trader_master_loader_v2"
BENCHMARK_SCHEMA_VERSION = "aibot_trader_master_benchmark_v1"


class FieldClassification(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class CanonicalFieldSpec:
    name: str
    classification: FieldClassification
    aliases: tuple[str, ...] = ()
    pre_trade_attribute: bool = False
    post_trade_outcome: bool = False


CANONICAL_FIELD_SPECS: tuple[CanonicalFieldSpec, ...] = (
    CanonicalFieldSpec("source_investment_id", FieldClassification.DERIVED),
    CanonicalFieldSpec("source_batch_id", FieldClassification.DERIVED),
    CanonicalFieldSpec("exchange", FieldClassification.OPTIONAL, ("exchange", "exchange_source"), True),
    CanonicalFieldSpec("bot_instance_id", FieldClassification.OPTIONAL, ("bot_instance_id",), True),
    CanonicalFieldSpec("strategy_family", FieldClassification.OPTIONAL, ("strategy_family", "strategy"), True),
    CanonicalFieldSpec("order_id", FieldClassification.OPTIONAL, ("order_id",)),
    CanonicalFieldSpec("trade_id", FieldClassification.OPTIONAL, ("trade_id", "source_trade_id")),
    CanonicalFieldSpec("symbol", FieldClassification.REQUIRED, ("symbol", "moeda", "pair"), True),
    CanonicalFieldSpec("side", FieldClassification.REQUIRED, ("side", "fechar_side"), True),
    CanonicalFieldSpec("leverage", FieldClassification.OPTIONAL, ("leverage", "alavancagem"), True),
    CanonicalFieldSpec("open_time_utc", FieldClassification.REQUIRED, ("open_time", "horario_abertura"), True),
    CanonicalFieldSpec("close_time_utc", FieldClassification.REQUIRED, ("close_time", "horario_fechamento"), False, True),
    CanonicalFieldSpec("open_rate", FieldClassification.OPTIONAL, ("open_rate", "preco_abertura"), True),
    CanonicalFieldSpec("close_rate", FieldClassification.OPTIONAL, ("close_rate", "preco_fechamento"), False, True),
    CanonicalFieldSpec("stake", FieldClassification.OPTIONAL, ("stake", "stake_amount"), True),
    CanonicalFieldSpec("notional", FieldClassification.OPTIONAL, ("notional", "raw_notional"), True),
    CanonicalFieldSpec("pnl_gross", FieldClassification.OPTIONAL, ("pnl_gross", "gross_pnl"), False, True),
    CanonicalFieldSpec("fees", FieldClassification.OPTIONAL, ("fees", "trading_fee"), False, True),
    CanonicalFieldSpec("funding", FieldClassification.OPTIONAL, ("funding", "funding_fee", "funding_fees"), False, True),
    CanonicalFieldSpec("pnl_net", FieldClassification.REQUIRED, ("pnl_net", "net_pnl", "pnl_fechado"), False, True),
    CanonicalFieldSpec("exit_reason", FieldClassification.OPTIONAL, ("exit_reason", "close_reason"), False, True),
    CanonicalFieldSpec("duration_seconds", FieldClassification.DERIVED, (), False, True),
    CanonicalFieldSpec("source_row_number", FieldClassification.DERIVED),
)

PRE_TRADE_ATTRIBUTES = tuple(
    spec.name for spec in CANONICAL_FIELD_SPECS if spec.pre_trade_attribute
)
POST_TRADE_OUTCOMES = tuple(
    spec.name for spec in CANONICAL_FIELD_SPECS if spec.post_trade_outcome
) + ("mfe", "mae", "winner", "realized_return", "future_regime_label")


@dataclass(frozen=True)
class SourceArtifactRecord:
    source_investment_id: str
    source_batch_id: str
    source_artifact_path: str
    source_artifact_name: str
    source_artifact_sha256: str
    source_artifact_size_bytes: int
    source_artifact_modified_at: str
    loaded_at_utc: str
    source_row_count: int
    schema_version: str = SOURCE_REGISTRY_SCHEMA_VERSION
    loader_version: str = LOADER_VERSION
    source_account_alias: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TraderMasterLoadResult:
    source: SourceArtifactRecord
    frame: "pd.DataFrame"
    audit: dict[str, Any]
    adapter_report: dict[str, Any]


def safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "live": False,
        "canary": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "trains_model": False,
        "changes_model": False,
        "changes_risk": False,
        "writes_trader_master": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_active_signals": False,
    }
