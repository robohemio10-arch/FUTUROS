from __future__ import annotations

import argparse
import json
from smartcrypto.ops.paper_session import collect_evidence

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()
    print(json.dumps(collect_evidence(session_id=args.session_id), ensure_ascii=False, indent=2))
