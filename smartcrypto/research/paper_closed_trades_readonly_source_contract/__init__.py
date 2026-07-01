"""Paper closed trades read-only source contract V1."""

from smartcrypto.research.paper_closed_trades_readonly_source_contract.source_contract import (
    DEFAULT_MARKDOWN_REPORT,
    DEFAULT_OUTPUT_REPORT,
    SCHEMA_VERSION,
    build_paper_closed_trades_readonly_source_contract_report,
    compute_source_contract,
    load_closed_trade_source_candidates,
    normalize_closed_trade_rows,
)

__all__ = [
    "DEFAULT_MARKDOWN_REPORT",
    "DEFAULT_OUTPUT_REPORT",
    "SCHEMA_VERSION",
    "build_paper_closed_trades_readonly_source_contract_report",
    "compute_source_contract",
    "load_closed_trade_source_candidates",
    "normalize_closed_trade_rows",
]
