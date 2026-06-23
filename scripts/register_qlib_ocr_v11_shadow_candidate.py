#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from smartcrypto.research.qlib_ocr_v11_shadow_candidate_registry import (
    ShadowCandidateRegistryConfig,
    resolve_paths,
    run_qlib_ocr_v11_shadow_candidate_registry,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register the Qlib OCR V1.1 candidate in a research-only shadow registry."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--training-summary")
    parser.add_argument("--executive-pack")
    parser.add_argument("--model-path")
    parser.add_argument("--registry-output")
    parser.add_argument("--report-output")
    parser.add_argument("--strict", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="Write research-only outputs.")
    mode.add_argument("--no-write", action="store_true", help="Validate in memory (default).")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(
        args.project_root,
        training_summary=args.training_summary,
        executive_pack=args.executive_pack,
        model_path=args.model_path,
        registry_output=args.registry_output,
        report_output=args.report_output,
    )
    try:
        result = run_qlib_ocr_v11_shadow_candidate_registry(
            paths,
            ShadowCandidateRegistryConfig(strict=bool(args.strict)),
            write=bool(args.write),
        )
    except Exception as exc:
        payload = {
            "status": "error",
            "reason": "unexpected_structural_error",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1
    encoded = json.dumps(result.report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    print(encoded if args.json else json.dumps(result.report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
