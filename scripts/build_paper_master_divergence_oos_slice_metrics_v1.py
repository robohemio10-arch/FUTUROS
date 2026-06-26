#!/usr/bin/env python3
"""Build research-only OOS slice metrics for Paper/Master divergence.

The CLI is standalone-safe and does not require PYTHONPATH or an editable
install when called as ``python scripts/<name>.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.paper_master_divergence_oos_slice_metrics import (  # noqa: E402
    run_oos_slice_metrics_research,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build research-only OOS slice metrics for the Paper/Master "
            "divergence without changing runtime or promoting rules."
        )
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input-json", help="Optional explicit research input JSON.")
    parser.add_argument("--output-path")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write an unversioned research report.")
    mode.add_argument(
        "--no-write",
        action="store_true",
        help="Evaluate in memory without writing (default).",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _safety_error_payload(exc: Exception) -> dict[str, object]:
    return {
        "schema_version": "paper_master_divergence_oos_slice_metrics_v1",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "invalid_oos_slice_metrics_structure",
        "error_type": type(exc).__name__,
        "research_only": True,
        "read_only": True,
        "paper_only": True,
        "shadow_only": True,
        "operational_authority": False,
        "can_apply_to_freqtrade": False,
        "can_apply_to_risk_manager": False,
        "can_promote_rules": False,
        "can_promote_model": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "updates_freqtrade": False,
        "updates_risk_manager": False,
        "updates_qlib_runtime": False,
        "updates_ai_shadow_runtime": False,
        "write_performed": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_oos_slice_metrics_research(
            args.project_root,
            write=bool(args.write),
            output_path=args.output_path,
            input_path=args.input_json,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                _safety_error_payload(exc),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
        return 1

    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if args.json else 2,
            sort_keys=True,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
