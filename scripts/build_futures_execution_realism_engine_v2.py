"""Run the deterministic research-only futures execution realism engine V2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.futures_execution_realism_v2.pipeline import (  # noqa: E402
    DEFAULT_JSON,
    DEFAULT_MANIFEST_ROOT,
    DEFAULT_MARKDOWN,
    build_futures_execution_realism_report,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-root", default=".")
    value.add_argument("--seed", type=int, default=42)
    value.add_argument(
        "--input-mode",
        choices=("synthetic_fixture", "legacy_quarantined"),
        default="synthetic_fixture",
    )
    value.add_argument("--write-report", action="store_true")
    value.add_argument("--output-json", default=DEFAULT_JSON)
    value.add_argument("--output-markdown", default=DEFAULT_MARKDOWN)
    value.add_argument("--manifest-output-root", default=DEFAULT_MANIFEST_ROOT)
    value.add_argument("--json", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = build_futures_execution_realism_report(
        project_root=args.project_root,
        write_report=args.write_report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        manifest_output_root=args.manifest_output_root,
        seed=args.seed,
        input_mode=args.input_mode,
        command="scripts/build_futures_execution_realism_engine_v2.py",
        arguments=sys.argv[1:] if argv is None else argv,
    )
    if args.json:
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(f"STATUS={report['status']}")
        print(f"REASON={report['reason']}")
        print(f"AUTHORITATIVE_RESULT={report['authoritative_result']}")
        print(f"WRITE_PERFORMED={report['write_performed']}")
    return 0 if report["status"] in {"ok", "warning"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
