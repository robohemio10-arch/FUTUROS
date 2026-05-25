from __future__ import annotations

import json
import time
from datetime import datetime, timezone


def main() -> None:
    while True:
        print(
            json.dumps(
                {
                    "service": "qlib-worker-paper",
                    "status": "idle",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "hint": "Use paper_controlado_fase_08 scripts to run Qlib training and prediction.",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(60)


if __name__ == "__main__":
    main()
