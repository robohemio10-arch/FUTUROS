"""Static audit of Decision Ledger paper observability wiring points."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from smartcrypto.execution.decision_ledger_paper_observability_wiring_v1 import (
    DEFAULT_CONFIG_PATH,
    load_observability_config,
)

PRODUCERS = (
    "smartcrypto/execution/signal_producer.py",
    "smartcrypto/qlib_engine/signal_exporter.py",
    "smartcrypto/execution/signal_contract_guard.py",
)
STRATEGY = "freqtrade/user_data/strategies/SmartCryptoSignalStrategy.py"
PHASE14 = "scripts/run_phase14_runtime_feedback_sync.py"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit paper observability wiring statically.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def build_audit_report(project_root: Path, config_path: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=False)
    resolved_config = config_path if config_path.is_absolute() else root / config_path
    config = load_observability_config(resolved_config)
    producer_checks = [_producer_check(root / path, relative=path) for path in PRODUCERS]
    strategy_source = _read_python(root / STRATEGY)
    phase14_source = _read_python(root / PHASE14)
    strategy_checks = {
        "decision_event_id_preserved": "decision_event_id" in strategy_source,
        "signal_id_preserved": "signal_id" in strategy_source,
        "correlation_id_preserved": "correlation_id" in strategy_source,
        "legacy_writer_retained": "def _write_decision" in strategy_source,
    }
    phase14_checks = {
        "trade_link_adapter_present": "sync_phase14_trade_links_readonly" in phase14_source,
        "snapshot_source_passed": "snapshot_db=snapshot_output" in phase14_source,
    }
    all_ok = (
        all(item["wiring_order_valid"] for item in producer_checks)
        and all(strategy_checks.values())
        and all(phase14_checks.values())
        and not config.enabled
        and not config.writer_enabled
        and not config.trade_link_enabled
    )
    safety = config.safety_flags.model_dump(mode="json")
    return {
        "schema_version": "decision_ledger_paper_observability_wiring_audit_v1",
        "status": "ok" if all_ok else "blocked",
        "reason": "paper_observability_wiring_static_boundary_ok" if all_ok else "wiring_boundary_failed",
        "producer_checks": producer_checks,
        "strategy_checks": strategy_checks,
        "phase14_checks": phase14_checks,
        "enabled": config.enabled,
        "writer_enabled": config.writer_enabled,
        "trade_link_enabled": config.trade_link_enabled,
        "writer_invoked": False,
        "writes_runtime": False,
        "paper_behavior_changed": False,
        "static_audit_only": True,
        "safety_flags": safety,
        **safety,
    }


def _producer_check(path: Path, *, relative: str) -> dict[str, Any]:
    source = _read_python(path)
    prepare_position = source.find("prepare_before_risk_manager(")
    risk_position = source.find("apply_risk_manager_gate(", prepare_position + 1)
    finalize_position = source.find("finalize_after_risk_manager(", risk_position + 1)
    return {
        "path": relative,
        "imports_shared_coordinator": (
            "decision_ledger_paper_observability_wiring_v1" in source
        ),
        "prepare_before_risk_manager": prepare_position >= 0,
        "risk_manager_gate_present": risk_position >= 0,
        "finalize_after_risk_manager": finalize_position >= 0,
        "wiring_order_valid": (
            prepare_position >= 0
            and prepare_position < risk_position < finalize_position
        ),
    }


def _read_python(path: Path) -> str:
    source = path.read_text(encoding="utf-8-sig")
    ast.parse(source, filename=str(path))
    return source


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_audit_report(Path(args.project_root), Path(args.config))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}:{report['reason']}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
