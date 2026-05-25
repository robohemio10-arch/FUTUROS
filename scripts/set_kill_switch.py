from __future__ import annotations

import argparse
import json
from smartcrypto.risk.risk_manager import set_kill_switch

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enabled", choices=["true", "false"], required=True)
    parser.add_argument("--reason", default="manual_paper_control")
    parser.add_argument("--path", default="data/runtime/kill_switch.json")
    args = parser.parse_args()
    print(json.dumps(set_kill_switch(args.enabled == "true", args.reason, args.path), ensure_ascii=False, indent=2))
