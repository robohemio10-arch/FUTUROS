from __future__ import annotations

import json
from smartcrypto.risk.risk_manager import evaluate_risk

if __name__ == "__main__":
    decision = evaluate_risk(write_report=True)
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    if not decision.approved:
        raise SystemExit(2)
