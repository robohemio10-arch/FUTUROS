from __future__ import annotations

import json
from datetime import datetime, timezone

from smartcrypto.settings import RuntimeSettings


def main() -> None:
    settings = RuntimeSettings.from_env()
    settings.assert_safe()

    payload = {
        "status": "healthy",
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runtime_mode": settings.runtime_mode,
        "live_enabled": settings.live_enabled,
        "order_submission_enabled": settings.order_submission_enabled,
        "real_order_submission_enabled": settings.real_order_submission_enabled,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
