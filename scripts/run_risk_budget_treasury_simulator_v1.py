"""Run W9 Risk Intelligence + Treasury in offline shadow research mode."""

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

_ALLOWED_OUTPUT_ROOTS = (
    Path("data/research/risk_intelligence"),
    Path("data/reports/aibot_parity"),
)
_REQUIRED_FALSE = (
    "operational_authority",
    "sends_orders",
    "exchange_private_access",
    "changes_risk",
    "changes_model",
    "writes_active_signals",
    "live_release_allowed",
    "canary_release_allowed",
    "network_required",
    "risk_budget_operationally_applied",
    "treasury_operationally_applied",
)


def _load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("input_json_root_must_be_object")
    return raw


def _validate_config(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("config_root_must_be_mapping")
    if raw.get("mode") != "research":
        raise ValueError("unsafe_config:mode_must_be_research")
    for key in ("paper_only", "shadow_only", "research_only", "riskmanager_final_authority"):
        if raw.get(key) is not True:
            raise ValueError(f"unsafe_config:{key}_must_be_true")
    for key in _REQUIRED_FALSE:
        if raw.get(key) is not False:
            raise ValueError(f"unsafe_config:{key}_must_be_false")
    treasury = raw.get("treasury")
    if not isinstance(treasury, dict):
        raise ValueError("unsafe_config:treasury_mapping_required")
    if treasury.get("separate_from_strategy_pnl") is not True:
        raise ValueError("unsafe_config:treasury_must_be_separate_from_strategy_pnl")
    if treasury.get("reserve_can_mask_negative_expectancy") is not False:
        raise ValueError("unsafe_config:reserve_must_not_mask_negative_expectancy")
    return raw


def _safe_output(project_root: Path, output: Path) -> Path:
    root = project_root.resolve()
    target = output if output.is_absolute() else root / output
    target = target.resolve()
    allowed = [(root / item).resolve() for item in _ALLOWED_OUTPUT_ROOTS]
    if not any(target == item or item in target.parents for item in allowed):
        raise ValueError("output_path_not_authorized")
    return target


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                payload,
                stream,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
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
    parser.add_argument("--config", default="config/research/risk_intelligence.yaml")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(args.project_root).resolve()
    try:
        from smartcrypto.research.risk_intelligence import (
            RiskIntelligenceRequest,
            SnapshotStatus,
            build_snapshot,
        )

        _validate_config((root / args.config).resolve())
        request = RiskIntelligenceRequest.model_validate(
            _load_json(Path(args.input_json).resolve())
        )
        snapshot = build_snapshot(request)
        result: dict[str, Any] = {
            "status": snapshot.status.value,
            "reason": snapshot.reason,
            "snapshot": snapshot.model_dump(mode="json"),
            "write_requested": bool(args.output_json) and not args.no_write,
            "write_performed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
            "network_required": False,
            "network_calls_executed": False,
            "risk_budget_operationally_applied": False,
            "treasury_operationally_applied": False,
            "riskmanager_final_authority": True,
        }
        if args.output_json and not args.no_write:
            output = _safe_output(root, Path(args.output_json))
            result["write_performed"] = True
            result["output_json"] = str(output)
            _atomic_write_json(output, result)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 2 if snapshot.status == SnapshotStatus.BLOCKED else 0
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "reason": f"{type(exc).__name__}:{exc}",
            "write_performed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
            "network_required": False,
            "network_calls_executed": False,
            "risk_budget_operationally_applied": False,
            "treasury_operationally_applied": False,
            "riskmanager_final_authority": True,
        }
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
