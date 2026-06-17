from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smartcrypto.ml.dataset_manifest import (  # noqa: E402
    build_unified_dataset_manifest,
    write_manifest,
)
from smartcrypto.ml.unified_feature_contract import (  # noqa: E402
    CONTRACT_ID,
    CONTRACT_VERSION,
    FeatureValidationResult,
    UnifiedFeatureContract,
    UnifiedFeatureContractError,
    always_blocked_columns,
    build_contract_from_frame,
    read_table,
    safe_json,
    select_feature_columns,
    utc_timestamp,
    write_contract,
)

DEFAULT_QLIB_MARKET_FEATURES = Path("data/features/market_features_60d.parquet")
DEFAULT_QLIB_PREDICTIONS = Path("data/predictions/latest_qlib_predictions.parquet")
DEFAULT_AI_SHADOW_DATASET = Path("data/features/training_dataset_quality_gated_binance_1m.parquet")

REPORT_UNIFIED = "ai_unified_feature_contract.json"
REPORT_QLIB = "qlib_feature_contract.json"
REPORT_SHADOW = "ai_shadow_feature_contract.json"
REPORT_MANIFEST = "ai_unified_dataset_manifest.json"


def prepare_feature_matrix_for_contract(frame, *, drop_nan_rows: bool) -> tuple[Any, dict[str, Any]]:
    feature_columns = tuple(select_feature_columns(frame))
    diagnostics = {
        "source_rows": int(len(frame)),
        "contract_feature_columns": list(feature_columns),
        "contract_feature_count": len(feature_columns),
        "dropped_nan_rows": 0,
        "clean_rows": int(len(frame)),
        "nan_drop_enabled": drop_nan_rows,
    }
    if not feature_columns or always_blocked_columns(tuple(str(column) for column in frame.columns)):
        return frame, diagnostics

    prepared = frame.copy()
    if drop_nan_rows:
        before = len(prepared)
        prepared = prepared.dropna(subset=list(feature_columns))
        diagnostics["dropped_nan_rows"] = int(before - len(prepared))
        diagnostics["clean_rows"] = int(len(prepared))
    return prepared, diagnostics


def build_feature_contract_report(
    *,
    role: str,
    path: Path,
    report_path: Path | None,
    allow_nan: bool,
    allow_infinite: bool,
    drop_nan_rows: bool,
) -> dict[str, Any]:
    try:
        raw_frame = read_table(path)
        frame, diagnostics = prepare_feature_matrix_for_contract(raw_frame, drop_nan_rows=drop_nan_rows)
        contract = build_contract_from_frame(
            frame,
            dataset_role=role,
            source_dataset_path=path,
            allow_nan=allow_nan,
            allow_infinite=allow_infinite,
            role="qlib" if role.startswith("qlib") else "shadow" if role == "ai_shadow" else "shared",
        )
        validation = contract.validate_frame(frame)
        if validation.status != "ok":
            raise UnifiedFeatureContractError(validation.reason)
        if report_path is not None:
            write_contract(contract, report_path)
        return {
            "status": "ok",
            "reason": "ok",
            "role": role,
            "input_path": str(path),
            "report_path": str(report_path) if report_path is not None else None,
            "contract": contract.to_dict(),
            "validation": validation.to_dict(),
            "source_diagnostics": diagnostics,
            "write_performed": report_path is not None,
            "generated_at_utc": utc_timestamp(),
            **contract.safety_flags(),
        }
    except Exception as exc:
        report = {
            "status": "blocked",
            "reason": str(exc),
            "role": role,
            "input_path": str(path),
            "report_path": str(report_path) if report_path is not None else None,
            "contract": None,
            "validation": None,
            "write_performed": False,
            "generated_at_utc": utc_timestamp(),
            "paper_only": True,
            "shadow_only": True,
            "runtime_mode": "paper",
            "live_trading_enabled": False,
            "order_submission_enabled": False,
            "real_order_submission_enabled": False,
            "exchange_private_access": False,
            "sends_orders": False,
            "changes_risk": False,
            "changes_model": False,
            "changes_training_dataset": False,
            "writes_trades_master": False,
        }
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=safe_json),
                encoding="utf-8",
            )
        return report


def build_unified_contract_report(
    *,
    contracts: list[dict[str, Any]],
    report_path: Path | None,
) -> dict[str, Any]:
    errors = [f"{item['role']}:{item['reason']}" for item in contracts if item.get("status") != "ok"]
    feature_specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in contracts:
        contract_payload = item.get("contract") or {}
        for spec in contract_payload.get("features", []):
            name = str(spec["name"])
            if name in seen:
                continue
            seen.add(name)
            merged = dict(spec)
            merged["role"] = "shared"
            feature_specs.append(merged)

    unified_contract: dict[str, Any] | None = None
    if not errors and feature_specs:
        unified = UnifiedFeatureContract.from_dict(
            {
                "contract_id": CONTRACT_ID,
                "contract_version": CONTRACT_VERSION,
                "dataset_role": "unified",
                "features": feature_specs,
                "strict_order": True,
                "allow_extra_features": False,
                "allow_nan": False,
                "allow_infinite": False,
                "paper_only": True,
                "shadow_only": True,
                "runtime_mode": "paper",
                "live_trading_enabled": False,
                "order_submission_enabled": False,
                "real_order_submission_enabled": False,
                "exchange_private_access": False,
                "sends_orders": False,
                "changes_risk": False,
                "changes_model": False,
                "changes_training_dataset": False,
                "writes_trades_master": False,
            }
        ).with_hashes()
        unified_contract = unified.to_dict()
    elif not feature_specs:
        errors.append("unified_contract_has_no_features")

    status = "blocked" if errors else "ok"
    report = {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(errors))),
        "contract": unified_contract,
        "source_contract_statuses": [
            {"role": item.get("role"), "status": item.get("status"), "reason": item.get("reason")}
            for item in contracts
        ],
        "generated_at_utc": utc_timestamp(),
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=safe_json),
            encoding="utf-8",
        )
    return report


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    report_dir = None if args.no_write else args.report_dir
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)

    role_paths = {
        "qlib_market_features": args.market_features,
        "qlib_predictions": args.qlib_predictions,
        "ai_shadow": args.ai_shadow_dataset,
    }
    contract_reports = [
        build_feature_contract_report(
            role="qlib_market_features",
            path=args.market_features,
            report_path=None if report_dir is None else report_dir / REPORT_QLIB,
            allow_nan=args.allow_nan,
            allow_infinite=False,
            drop_nan_rows=not args.no_drop_nan,
        ),
        build_feature_contract_report(
            role="ai_shadow",
            path=args.ai_shadow_dataset,
            report_path=None if report_dir is None else report_dir / REPORT_SHADOW,
            allow_nan=args.allow_nan,
            allow_infinite=False,
            drop_nan_rows=not args.no_drop_nan,
        ),
        build_feature_contract_report(
            role="qlib_predictions",
            path=args.qlib_predictions,
            report_path=None,
            allow_nan=args.allow_nan,
            allow_infinite=False,
            drop_nan_rows=not args.no_drop_nan,
        ),
    ]
    unified = build_unified_contract_report(
        contracts=contract_reports,
        report_path=None if report_dir is None else report_dir / REPORT_UNIFIED,
    )
    manifest = build_unified_dataset_manifest(role_paths, strict=args.strict)
    if report_dir is not None:
        write_manifest(manifest, report_dir / REPORT_MANIFEST)

    validation_errors: list[str] = []
    if unified["status"] != "ok":
        validation_errors.append(f"unified_contract:{unified['reason']}")
    if manifest.status != "ok":
        validation_errors.append(f"dataset_manifest:{manifest.reason}")
    for item in contract_reports:
        if item["status"] != "ok":
            validation_errors.append(f"{item['role']}:{item['reason']}")

    status = "blocked" if validation_errors else "ok"
    return {
        "status": status,
        "reason": "ok" if status == "ok" else ";".join(sorted(set(validation_errors))),
        "branch": "codex/ai-unified-feature-contract-and-dataset-manifest",
        "project_root": str(args.project_root),
        "reports": {
            "ai_unified_feature_contract": str(report_dir / REPORT_UNIFIED) if report_dir is not None else None,
            "qlib_feature_contract": str(report_dir / REPORT_QLIB) if report_dir is not None else None,
            "ai_shadow_feature_contract": str(report_dir / REPORT_SHADOW) if report_dir is not None else None,
            "ai_unified_dataset_manifest": str(report_dir / REPORT_MANIFEST) if report_dir is not None else None,
        },
        "contract_reports": contract_reports,
        "unified_contract_report": unified,
        "dataset_manifest": manifest.to_dict(),
        "validation_errors": sorted(set(validation_errors)),
        "generated_at_utc": utc_timestamp(),
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_model": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "trains_model": False,
        "runs_inference": False,
        "changes_active_signals": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit unified feature contracts for Qlib + IA Shadow in paper/shadow mode."
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--market-features", type=Path, default=DEFAULT_QLIB_MARKET_FEATURES)
    parser.add_argument("--qlib-predictions", type=Path, default=DEFAULT_QLIB_PREDICTIONS)
    parser.add_argument("--ai-shadow-dataset", type=Path, default=DEFAULT_AI_SHADOW_DATASET)
    parser.add_argument("--report-dir", type=Path, default=Path("data/reports"))
    parser.add_argument("--strict", action="store_true", default=True)
    parser.add_argument("--allow-nan", action="store_true")
    parser.add_argument(
        "--no-drop-nan",
        action="store_true",
        help="Validate raw rows without dropping historical rolling warm-up NaNs from feature columns.",
    )
    parser.add_argument("--no-write", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=safe_json))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
