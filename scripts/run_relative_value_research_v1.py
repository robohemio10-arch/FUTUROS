"""CLI for deterministic W7 Relative Value research evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.relative_value import RelativeValueRequest, build_snapshot  # noqa: E402

_ALLOWED_OUTPUT_ROOTS = (
    Path("data/research/relative_value"),
    Path("data/reports/aibot_parity"),
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("input_json_must_be_object")
    return payload


def _validate_config(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config_must_be_mapping")
    required_true = ("paper_only", "shadow_only", "research_only")
    required_false = (
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
        "changes_risk",
        "changes_model",
        "writes_active_signals",
        "live",
        "canary",
        "network_required",
    )
    for key in required_true:
        if raw.get(key) is not True:
            raise ValueError(f"unsafe_config:{key}_must_be_true")
    for key in required_false:
        if raw.get(key) is not False:
            raise ValueError(f"unsafe_config:{key}_must_be_false")
    research = raw.get("research")
    if not isinstance(research, dict) or research.get("edge_claim_allowed") is not False:
        raise ValueError("unsafe_config:edge_claim_allowed_must_be_false")
    return raw


def _safe_output(project_root: Path, output: Path) -> Path:
    resolved_root = project_root.resolve()
    resolved_output = output if output.is_absolute() else (resolved_root / output)
    resolved_output = resolved_output.resolve()
    allowed = [(resolved_root / root).resolve() for root in _ALLOWED_OUTPUT_ROOTS]
    if not any(resolved_output == root or root in resolved_output.parents for root in allowed):
        raise ValueError("output_path_not_authorized")
    return resolved_output


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default="config/research/relative_value.yaml")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(args.project_root).resolve()
    try:
        _validate_config((root / args.config).resolve())
        request = RelativeValueRequest.model_validate(_load_json(Path(args.input_json).resolve()))
        snapshot = build_snapshot(request)
        result: dict[str, Any] = {
            "status": snapshot.status.value,
            "reason": snapshot.reason,
            "snapshot": snapshot.model_dump(mode="json"),
            "write_requested": bool(args.output_json) and not args.no_write,
            "write_performed": False,
            "network_required": False,
            "network_calls_executed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
            "edge_proven": False,
        }
        if args.output_json and not args.no_write:
            output = _safe_output(root, Path(args.output_json))
            result["write_performed"] = True
            result["output_json"] = str(output)
            _atomic_write_json(output, result)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 2 if snapshot.status.value == "BLOCKED" else 0
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "reason": f"{type(exc).__name__}:{exc}",
            "write_performed": False,
            "network_required": False,
            "network_calls_executed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
            "edge_proven": False,
        }
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
