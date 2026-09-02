#!/usr/bin/env python3
"""Build a deterministic no-training Market Intelligence ablation manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.research.market_intelligence import (  # noqa: E402
    MarketIntelligenceSnapshot,
    build_ablation_manifest,
    load_market_intelligence_config,
)
from smartcrypto.research.market_intelligence.ablation import (  # noqa: E402
    AblationPersistenceError,
    persist_ablation_manifest,
)

MAX_INPUT_BYTES = 8 * 1024 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default="config/research/market_intelligence.yaml")
    parser.add_argument("--input", required=True)
    parser.add_argument("--baseline-feature", action="append", default=[])
    parser.add_argument("--output-json")
    write_mode = parser.add_mutually_exclusive_group()
    write_mode.add_argument("--write-report", action="store_true")
    write_mode.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_snapshot(path_value: str | Path) -> MarketIntelligenceSnapshot:
    path = Path(path_value).expanduser().resolve(strict=False)
    if path.is_symlink():
        raise ValueError("market_intelligence_ablation_input_symlink_forbidden")
    if not path.is_file():
        raise ValueError("market_intelligence_ablation_input_missing")
    if path.suffix.casefold() != ".json":
        raise ValueError("market_intelligence_ablation_input_extension_invalid")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("market_intelligence_ablation_input_too_large")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("snapshot"), dict):
        payload = payload["snapshot"]
    if not isinstance(payload, dict):
        raise ValueError("market_intelligence_ablation_snapshot_missing")
    return MarketIntelligenceSnapshot.model_validate(payload)


def _blocked_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "write_requested": False,
        "write_performed": False,
        "training_performed": False,
        "model_promoted": False,
        "registry_write_performed": False,
        "paper_only": True,
        "shadow_only": True,
        "research_only": True,
        "operational_authority": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "changes_model": False,
        "writes_active_signals": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.project_root).resolve()
    try:
        load_market_intelligence_config(root, args.config)
        snapshot = _load_snapshot(args.input)
        manifest = build_ablation_manifest(
            snapshot,
            baseline_feature_names=tuple(args.baseline_feature),
        )
        write_performed = False
        output_paths: dict[str, str] = {}
        if args.write_report:
            persisted = persist_ablation_manifest(
                project_root=root,
                manifest=manifest,
                output_json=args.output_json,
            )
            write_performed = bool(persisted["write_performed"])
            output_paths = dict(persisted["output_paths"])
        payload = {
            "status": manifest.status,
            "reason": manifest.reason,
            "manifest": manifest.model_dump(mode="json"),
            "write_requested": bool(args.write_report),
            "write_performed": write_performed,
            "output_paths": output_paths,
            "training_performed": False,
            "model_promoted": False,
            "registry_write_performed": False,
            "paper_only": True,
            "shadow_only": True,
            "research_only": True,
            "operational_authority": False,
            "sends_orders": False,
            "exchange_private_access": False,
            "changes_risk": False,
            "changes_model": False,
            "writes_active_signals": False,
        }
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        ValidationError,
        AblationPersistenceError,
    ) as exc:
        reason = str(exc).splitlines()[0][:240] or type(exc).__name__
        payload = _blocked_payload(reason)
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
            allow_nan=False,
        )
    )
    return 0 if payload["status"] in {"ABLATION_DATA_READY", "NO_AVAILABLE_FEATURES"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
