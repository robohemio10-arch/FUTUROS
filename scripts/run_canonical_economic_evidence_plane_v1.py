#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from smartcrypto.research.canonical_economic_plane.orchestrator import (
    PlaneConfig,
    run_economic_evidence_cycle,
)


def _path(value: str | None) -> Path | None:
    return Path(value) if value else None


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run the research-only continuous economic evidence plane."
    )
    value.add_argument("--project-root", default=".")
    value.add_argument("--data-root", default="data")
    value.add_argument(
        "--output-dir",
        default="data/reports/canonical_economic_plane",
    )
    value.add_argument("--master", default=None)
    value.add_argument("--paper-csv", default=None)
    value.add_argument("--paper-snapshot-sqlite", default=None)
    value.add_argument("--dataset", default=None)
    value.add_argument("--feature-contract", default=None)
    value.add_argument("--dataset-manifest", default=None)
    value.add_argument("--split-manifest", default=None)
    value.add_argument("--shadow-evidence", default=None)
    value.add_argument("--sleeve-evidence", default=None)
    value.add_argument("--council-evidence", default=None)
    value.add_argument("--treatment-monitor", default=None)
    value.add_argument("--write", action="store_true")
    value.add_argument("--daemon", action="store_true")
    value.add_argument("--interval-seconds", type=int, default=900)
    value.add_argument("--json", action="store_true")
    return value


def _config(args: argparse.Namespace) -> PlaneConfig:
    return PlaneConfig(
        project_root=Path(args.project_root),
        data_root=Path(args.data_root),
        output_dir=Path(args.output_dir),
        master_path=_path(args.master),
        paper_csv_path=_path(args.paper_csv),
        paper_snapshot_sqlite_path=_path(args.paper_snapshot_sqlite),
        dataset_path=_path(args.dataset),
        feature_contract_path=_path(args.feature_contract),
        dataset_manifest_path=_path(args.dataset_manifest),
        split_manifest_path=_path(args.split_manifest),
        shadow_evidence_path=_path(args.shadow_evidence),
        sleeve_evidence_path=_path(args.sleeve_evidence),
        council_evidence_path=_path(args.council_evidence),
        treatment_monitor_path=_path(args.treatment_monitor),
    )


def _emit(report: dict[str, object], compact: bool) -> None:
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            default=str,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        ),
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.interval_seconds < 60:
        raise SystemExit("interval_seconds_must_be_at_least_60")
    if args.daemon and not args.write:
        raise SystemExit("daemon_requires_write")

    config = _config(args)

    if not args.daemon:
        report = run_economic_evidence_cycle(config, write_reports=args.write)
        _emit(report, args.json)
        return 0

    while True:
        report = run_economic_evidence_cycle(config, write_reports=True)
        _emit(report, True)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
