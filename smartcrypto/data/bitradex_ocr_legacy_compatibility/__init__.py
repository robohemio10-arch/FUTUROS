"""Read-only compatibility audit for the historical Bitradex OCR contract."""

from .compatibility_audit import (
    DEFAULT_CONTRACT_PATH,
    DEFAULT_OUTPUT_JSON,
    DEFAULT_OUTPUT_MARKDOWN,
    build_legacy_compatibility_audit,
    render_markdown,
)
from .contract import (
    LEGACY_CONTRACT_ID,
    LEGACY_CONTRACT_SCHEMA_VERSION,
    LegacyContract,
    LegacyContractError,
    load_legacy_contract,
)

__all__ = [
    "DEFAULT_CONTRACT_PATH",
    "DEFAULT_OUTPUT_JSON",
    "DEFAULT_OUTPUT_MARKDOWN",
    "LEGACY_CONTRACT_ID",
    "LEGACY_CONTRACT_SCHEMA_VERSION",
    "LegacyContract",
    "LegacyContractError",
    "build_legacy_compatibility_audit",
    "load_legacy_contract",
    "render_markdown",
]
