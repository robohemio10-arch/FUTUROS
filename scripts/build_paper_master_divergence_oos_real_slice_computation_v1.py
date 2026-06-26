#!/usr/bin/env python3
"""CLI for research-only real OOS slice computation of Paper/Master divergence."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_master_divergence_oos_real_slice_computation.real_slice_computation import (  # noqa: E501
    main,
)


if __name__ == "__main__":
    raise SystemExit(main())
