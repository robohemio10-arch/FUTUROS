#!/usr/bin/env python3
"""CLI wrapper for OCR Master + candle aligned OOS research V1."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.ocr_master_candle_aligned_oos_research.master_candle_oos_research import main


if __name__ == "__main__":
    raise SystemExit(main())
