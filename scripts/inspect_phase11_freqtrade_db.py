#!/usr/bin/env python3
"""Inspect the paper Freqtrade SQLite database in read-only mode.

This script is intentionally standalone-safe: it can be executed directly as
``python scripts/inspect_phase11_freqtrade_db.py`` without relying on an
editable install or PYTHONPATH.

Safety contract:
- read-only inspection only;
- no order submission;
- no exchange private access;
- no Freqtrade runtime mutation;
- no RiskManager mutation;
- no Qlib runtime mutation;
- no AI Shadow runtime mutation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.freqtrade_db_reader import inspect_freqtrade_db


def build_phase11_freqtrade_db_inspection_report() -> dict[str, Any]:
    """Return a read-only Freqtrade database inspection report."""
    report = inspect_freqtrade_db()
    return {
        **report,
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "updates_freqtrade": False,
        "updates_qlib_runtime": False,
        "updates_risk_manager": False,
        "updates_ai_shadow_runtime": False,
        "operational_authority": False,
        "read_only": True,
    }


def main() -> int:
    report = build_phase11_freqtrade_db_inspection_report()
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
