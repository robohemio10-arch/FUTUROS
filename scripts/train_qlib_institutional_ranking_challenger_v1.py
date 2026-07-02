#!/usr/bin/env python3
"""Train or validate a research-only Qlib institutional ranking challenger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.learning.qlib_trainer import build_qlib_institutional_ranking_trainer_report  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="Project root. Defaults to current directory.")
    parser.add_argument("--feature-contract", default=None)
    parser.add_argument("--dataset-manifest", default=None)
    parser.add_argument("--target-store", default=None)
    parser.add_argument("--walkforward", default=None)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--report-json", default=None)
    parser.add_argument("--report-markdown", default=None)
    parser.add_argument("--metrics-json", default=None)
    parser.add_argument("--metrics-markdown", default=None)
    parser.add_argument("--train", action="store_true", help="Train a research-only challenger.")
    parser.add_argument("--write-report", action="store_true", help="Write report JSON/Markdown under data/reports.")
    parser.add_argument("--write-challenger-artifact", action="store_true", help="Write challenger artifact under data/models/challengers; requires --train.")
    parser.add_argument("--allow-research-fallback", action="store_true", help="Allow deterministic research fallback when qlib is unavailable.")
    parser.add_argument("--request-registry-write", action="store_true", help="Always blocked in this branch.")
    parser.add_argument("--request-model-promotion", action="store_true", help="Always blocked in this branch.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_qlib_institutional_ranking_trainer_report(
        project_root=args.project_root,
        train=args.train,
        write_report=args.write_report,
        write_challenger_artifact=args.write_challenger_artifact,
        allow_research_fallback=args.allow_research_fallback,
        feature_contract_path=args.feature_contract,
        dataset_manifest_path=args.dataset_manifest,
        target_store_path=args.target_store,
        walkforward_path=args.walkforward,
        baseline_path=args.baseline,
        dataset_path=args.dataset,
        report_json_path=args.report_json,
        report_markdown_path=args.report_markdown,
        metrics_json_path=args.metrics_json,
        metrics_markdown_path=args.metrics_markdown,
        registry_write_requested=args.request_registry_write,
        model_promotion_requested=args.request_model_promotion,
    )
    payload = cli_payload(report)
    if args.json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str))
    return 0


def cli_payload(report: dict) -> dict:
    payload = dict(report)
    metrics = payload.get("metrics_by_split")
    if isinstance(metrics, list):
        payload["metrics_by_split_count"] = len(metrics)
        payload["metrics_by_split_sample"] = metrics[:3]
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
