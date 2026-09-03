from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MAX_SOURCE_BYTES = 64 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic SMART FUTUROS AIBOT-Parity W13 pipeline "
            "from explicit read-only source snapshots."
        )
    )
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--decision-time-utc", required=True)
    parser.add_argument("--request-id")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Explicit JSON source. Repeat for each W1-W9 snapshot/report.",
    )
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    from smartcrypto.research.aibot_parity_orchestrator import (
        ALLOWED_SOURCE_NAMES,
        AibotParityPipelineRequest,
        PipelineStatus,
        build_aibot_parity_pipeline,
        persist_pipeline_snapshot,
    )

    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve()
    decision_time = _parse_utc(args.decision_time_utc)
    sources = _load_sources(root, args.source, ALLOWED_SOURCE_NAMES)
    request_id = args.request_id or (
        "aibot-parity-" + decision_time.strftime("%Y%m%dT%H%M%SZ")
    )
    request = AibotParityPipelineRequest(
        request_id=request_id,
        decision_time_utc=decision_time,
        sources=sources,
    )
    snapshot = build_aibot_parity_pipeline(request)
    write_result = {
        "write_performed": False,
        "lock_serialized": True,
        "output_path": None,
    }
    if args.write_report:
        write_result = persist_pipeline_snapshot(
            project_root=root,
            snapshot=snapshot,
            output_json=args.output_json,
        )
    payload = {
        **snapshot.model_dump(mode="json"),
        **write_result,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 1 if snapshot.status is PipelineStatus.BLOCKED else 0


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit("invalid --decision-time-utc") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit("--decision-time-utc must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _load_sources(
    root: Path,
    source_args: list[str],
    allowed_names: frozenset[str],
) -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for raw in source_args:
        if "=" not in raw:
            raise SystemExit("--source must use NAME=PATH")
        name, raw_path = raw.split("=", 1)
        name = name.strip()
        if name not in allowed_names:
            raise SystemExit(f"unknown --source name: {name}")
        if name in loaded:
            raise SystemExit(f"duplicate --source name: {name}")
        path = _resolve_source_path(root, raw_path)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise SystemExit(f"source too large: {name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid source JSON: {name}") from exc
        if not isinstance(payload, dict):
            raise SystemExit(f"source root must be object: {name}")
        loaded[name] = payload
    return loaded


def _resolve_source_path(root: Path, raw_path: str) -> Path:
    path = Path(raw_path.strip())
    candidate = path if path.is_absolute() else root / path
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SystemExit("source path outside project root") from exc
    if candidate.is_symlink():
        raise SystemExit("source symlink forbidden")
    if not candidate.is_file():
        raise SystemExit("source file missing")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
