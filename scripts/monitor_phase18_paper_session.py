from __future__ import annotations

import json
from smartcrypto.ops.paper_session import build_session_state

if __name__ == "__main__":
    print(json.dumps(build_session_state(), ensure_ascii=False, indent=2, default=str))
