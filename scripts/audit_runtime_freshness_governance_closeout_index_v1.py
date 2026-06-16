
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smartcrypto.ops.dashboard_snapshots.runtime_freshness_governance_closeout_index import (  # noqa: E402
    REPORT_PATH,
    build_runtime_freshness_governance_closeout_index,
    load_runtime_freshness_governance_closeout_index_inputs,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consolida o índice read-only de governança/freshness runtime."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path(args.project_root).resolve()
    payload = build_runtime_freshness_governance_closeout_index(
        now_utc=datetime.now(timezone.utc),
        **load_runtime_freshness_governance_closeout_index_inputs(project_root),
    )
    if args.write_report:
        _write_report(project_root, payload)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{payload['status']}: {payload['reason']}")
    return 1 if payload.get("status") == "blocked" else 0


def _write_report(project_root: Path, payload: dict[str, object]) -> None:
    report_path = (project_root / REPORT_PATH).resolve()
    report_root = (project_root / "data/reports").resolve()
    if report_path.parent != report_root:
        raise ValueError(f"unauthorized_report_path:{report_path}")
    report_root.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)


if __name__ == "__main__":
    raise SystemExit(main())
