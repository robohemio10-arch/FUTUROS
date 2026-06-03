from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartcrypto.ml.drift_monitor import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_CURRENT_PATH,
    DEFAULT_KS_BLOCKED,
    DEFAULT_KS_WARNING,
    DEFAULT_PSI_BLOCKED,
    DEFAULT_PSI_WARNING,
    DEFAULT_REPORT_PATH,
    run_ai_shadow_drift_monitor,
)


DEFAULT_CONTRACT_PATH = Path("data/models/shadow/ai_shadow_feature_contract.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run IA Shadow drift monitor.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT_PATH)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--psi-warning", type=float, default=DEFAULT_PSI_WARNING)
    parser.add_argument("--psi-blocked", type=float, default=DEFAULT_PSI_BLOCKED)
    parser.add_argument("--ks-warning", type=float, default=DEFAULT_KS_WARNING)
    parser.add_argument("--ks-blocked", type=float, default=DEFAULT_KS_BLOCKED)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract_path = args.contract if args.contract.exists() else None
    report = run_ai_shadow_drift_monitor(
        baseline_path=args.baseline,
        current_path=args.current,
        contract_path=contract_path,
        report_path=args.report,
        psi_warning=args.psi_warning,
        psi_blocked=args.psi_blocked,
        ks_warning=args.ks_warning,
        ks_blocked=args.ks_blocked,
        strict=args.strict,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"ok", "warning"} else 1


if __name__ == "__main__":
    sys.exit(main())
