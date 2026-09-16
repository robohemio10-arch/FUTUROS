"""Official post-OCR trades-master research contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Final


OFFICIAL_MASTER_SHA256: Final[str] = (
    "a99c03497bb47e2aee2762c182ed45e38060490914d6991d3730d31de649935f"
)

OFFICIAL_MASTER_COLUMNS: Final[tuple[str, ...]] = (
    "master_schema_version",
    "master_candidate_status",
    "trade_sequence",
    "image_number",
    "source_file",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
    "taxa_1",
    "order_id_raw",
    "moeda",
    "fechar_side",
    "symbol_raw",
    "symbol",
    "side_raw",
    "side",
    "classification",
    "fee_semantics_regime",
    "reported_pnl",
    "economic_fee_adjustment",
    "economic_net_pnl",
    "fee_policy_version",
    "fee_policy_formula",
    "fee_semantics_provenance",
    "fee_semantics_gate",
    "economic_pnl_canonicalized",
    "cumulative_economic_net_pnl",
    "running_peak_economic_net_pnl",
    "economic_drawdown_pnl",
    "final_dedup_group_id",
    "final_dedup_classification",
    "final_dedup_canonical_image",
    "final_dedup_keep",
    "final_dedup_excluded",
    "final_dedup_gate",
    "trades_master_write_allowed",
)

OFFICIAL_MASTER_SHEETS: Final[tuple[str, ...]] = (
    "TRADES",
    "METADATA",
    "AUDIT",
)

LEGACY_OPENING_SENTINEL: Final[str] = "SOURCE_NOT_AVAILABLE_FROM_PRINT"

DURATION_BUCKET_EDGES_SECONDS: Final[tuple[float, ...]] = (
    float("-inf"),
    15.0 * 60.0,
    30.0 * 60.0,
    60.0 * 60.0,
    3.0 * 60.0 * 60.0,
    6.0 * 60.0 * 60.0,
    float("inf"),
)

DURATION_BUCKET_LABELS: Final[tuple[str, ...]] = (
    "<15m",
    "15-30m",
    "30-60m",
    "1-3h",
    "3-6h",
    ">6h",
)


@dataclass(frozen=True)
class MasterSourceContract:
    expected_sha256: str
    expected_rows: int
    expected_columns: int
    expected_sheets: tuple[str, ...]
    expected_trades_dimension: str
    expected_column_order: tuple[str, ...]
    expected_formula_count: int
    economic_identity_abs_tolerance: float
    allowed_fee_regimes: tuple[str, ...]
    required_traceability_columns: tuple[str, ...]
    order_id_pattern: str


@dataclass(frozen=True)
class OfficialBaselineContract:
    expected_rows: int
    expected_reported_pnl_total: float
    expected_fee_adjustment_total: float
    expected_economic_net_pnl_total: float
    expected_profit_factor: float
    expected_win_rate: float
    expected_max_drawdown_signed: float
    expected_opening_available: int
    expected_opening_unavailable: int
    expected_standard_count: int
    expected_legacy_count: int
    financial_abs_tolerance: float
    profit_factor_abs_tolerance: float
    win_rate_abs_tolerance: float
    cumulative_abs_tolerance: float
    legacy_opening_sentinel: str
    duration_bucket_edges_seconds: tuple[float, ...]
    duration_bucket_labels: tuple[str, ...]


CANONICAL_SOURCE_CONTRACT: Final[MasterSourceContract] = MasterSourceContract(
    expected_sha256=OFFICIAL_MASTER_SHA256,
    expected_rows=3991,
    expected_columns=41,
    expected_sheets=OFFICIAL_MASTER_SHEETS,
    expected_trades_dimension="A1:AO3992",
    expected_column_order=OFFICIAL_MASTER_COLUMNS,
    expected_formula_count=0,
    economic_identity_abs_tolerance=1e-9,
    allowed_fee_regimes=("LEGACY", "STANDARD"),
    required_traceability_columns=(
        "trade_sequence",
        "image_number",
        "source_file",
        "order_id_raw",
    ),
    order_id_pattern=r"^[0-9A-Fa-f]{24}$",
)

CANONICAL_BASELINE_CONTRACT: Final[OfficialBaselineContract] = (
    OfficialBaselineContract(
        expected_rows=3991,
        expected_reported_pnl_total=3644.918934,
        expected_fee_adjustment_total=-461.069030,
        expected_economic_net_pnl_total=3183.849904,
        expected_profit_factor=1.6336472011,
        expected_win_rate=0.66424455,
        expected_max_drawdown_signed=-244.315270,
        expected_opening_available=3760,
        expected_opening_unavailable=231,
        expected_standard_count=3760,
        expected_legacy_count=231,
        financial_abs_tolerance=1e-6,
        profit_factor_abs_tolerance=1e-9,
        win_rate_abs_tolerance=1e-8,
        cumulative_abs_tolerance=1e-8,
        legacy_opening_sentinel=LEGACY_OPENING_SENTINEL,
        duration_bucket_edges_seconds=DURATION_BUCKET_EDGES_SECONDS,
        duration_bucket_labels=DURATION_BUCKET_LABELS,
    )
)


def contract_sha256(
    contract: MasterSourceContract | OfficialBaselineContract,
) -> str:
    payload = json.dumps(
        asdict(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CANONICAL_BASELINE_CONTRACT",
    "CANONICAL_SOURCE_CONTRACT",
    "DURATION_BUCKET_EDGES_SECONDS",
    "DURATION_BUCKET_LABELS",
    "LEGACY_OPENING_SENTINEL",
    "MasterSourceContract",
    "OFFICIAL_MASTER_COLUMNS",
    "OFFICIAL_MASTER_SHA256",
    "OFFICIAL_MASTER_SHEETS",
    "OfficialBaselineContract",
    "contract_sha256",
]
