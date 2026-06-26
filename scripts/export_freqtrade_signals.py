from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.bot import run_once
from smartcrypto.settings import RuntimeSettings


if __name__ == "__main__":
    run_once(RuntimeSettings.from_env())
