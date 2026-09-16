"""Official post-OCR trades-master research package."""

from .baseline import (
    OfficialBaselineValidationError,
    build_official_trades_master_baseline,
)
from .contracts import (
    CANONICAL_BASELINE_CONTRACT,
    CANONICAL_SOURCE_CONTRACT,
    OFFICIAL_MASTER_SHA256,
)
from .loader import (
    OfficialMasterData,
    OfficialMasterValidationError,
    SourceAudit,
    load_official_trades_master,
)
from .persistence import (
    persist_baseline_reports,
    report_content_sha256,
)

__all__ = [
    "CANONICAL_BASELINE_CONTRACT",
    "CANONICAL_SOURCE_CONTRACT",
    "OFFICIAL_MASTER_SHA256",
    "OfficialBaselineValidationError",
    "OfficialMasterData",
    "OfficialMasterValidationError",
    "SourceAudit",
    "build_official_trades_master_baseline",
    "load_official_trades_master",
    "persist_baseline_reports",
    "report_content_sha256",
]
