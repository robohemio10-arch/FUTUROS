from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse
import json
from pathlib import Path

from smartcrypto.ml.ai_shadow_outcome_attribution import (
    DEFAULT_DATASET_PATH,
    DEFAULT_DECISIONS_PATH,
    DEFAULT_REPORT_PATH,
    AttributionConfig,
    json_safe,
    run_ai_shadow_outcome_attribution,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only AI Shadow outcome attribution.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS_PATH))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--strict-alignment", action="store_true")
    args = parser.parse_args()

    result = run_ai_shadow_outcome_attribution(
        AttributionConfig(
            dataset_path=Path(args.dataset),
            decisions_path=Path(args.decisions),
            report_path=Path(args.report_json),
            strict_alignment=bool(args.strict_alignment),
        )
    )

    print(json.dumps(json_safe(result), indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
