"""Lineage validation for the canonical auto-learning closeout."""

from __future__ import annotations

from typing import Any


LINEAGE_KEYS = ("feature_contract_hash", "dataset_hash", "target_store_hash", "split_engine_hash")


def validate_lineage(payloads: dict[str, dict[str, Any]], source_hashes: dict[str, str | None]) -> dict[str, Any]:
    """Validate cross-stage hashes without rebuilding upstream artifacts."""

    canonical = {
        "feature_contract_hash": payloads.get("feature_contract", {}).get("contract_hash"),
        "dataset_hash": payloads.get("dataset_manifest", {}).get("dataset_hash"),
        "target_store_hash": payloads.get("target_store", {}).get("target_store_hash"),
        "split_engine_hash": payloads.get("walkforward_split", {}).get("split_engine_hash"),
        "qlib_trainer_report_hash": source_hashes.get("qlib_trainer"),
        "ai_shadow_trainer_report_hash": source_hashes.get("ai_shadow_trainer"),
    }

    checks: list[dict[str, Any]] = []
    add_hash_check(checks, "dataset_manifest", "feature_contract_hash", canonical["feature_contract_hash"], payloads)
    add_hash_check(checks, "target_store", "feature_contract_hash", canonical["feature_contract_hash"], payloads)
    add_hash_check(checks, "target_store", "dataset_hash", canonical["dataset_hash"], payloads)
    add_hash_check(checks, "walkforward_split", "feature_contract_hash", canonical["feature_contract_hash"], payloads)
    add_hash_check(checks, "walkforward_split", "dataset_hash", canonical["dataset_hash"], payloads)
    add_hash_check(checks, "walkforward_split", "target_store_hash", canonical["target_store_hash"], payloads)
    for stage_id in ("qlib_trainer", "ai_shadow_trainer"):
        add_hash_check(checks, stage_id, "feature_contract_hash", canonical["feature_contract_hash"], payloads)
        add_hash_check(checks, stage_id, "dataset_hash", canonical["dataset_hash"], payloads)
        add_hash_check(checks, stage_id, "target_store_hash", canonical["target_store_hash"], payloads)
        add_hash_check(checks, stage_id, "split_engine_hash", canonical["split_engine_hash"], payloads)
    qlib_hash = payloads.get("ai_shadow_trainer", {}).get("qlib_trainer_report_hash")
    if qlib_hash:
        checks.append(
            {
                "stage_id": "ai_shadow_trainer",
                "field": "qlib_trainer_report_hash",
                "expected": canonical["qlib_trainer_report_hash"],
                "actual": qlib_hash,
                "status": "ok" if qlib_hash == canonical["qlib_trainer_report_hash"] else "blocked",
            }
        )

    missing_hashes = [key for key, value in canonical.items() if key != "ai_shadow_trainer_report_hash" and not value]
    drift_checks = [check for check in checks if check["status"] == "blocked"]
    status = "blocked" if missing_hashes or drift_checks else "ok"
    return {
        "lineage_status": status,
        "lineage_drift_detected": status != "ok",
        "lineage_matrix": checks,
        "missing_lineage_hashes": missing_hashes,
        **canonical,
    }


def add_hash_check(
    checks: list[dict[str, Any]],
    stage_id: str,
    field: str,
    expected: str | None,
    payloads: dict[str, dict[str, Any]],
) -> None:
    actual = payloads.get(stage_id, {}).get(field)
    if actual is None and expected is None:
        status = "blocked"
    elif actual is None:
        status = "blocked"
    elif expected is None:
        status = "blocked"
    else:
        status = "ok" if actual == expected else "blocked"
    checks.append({"stage_id": stage_id, "field": field, "expected": expected, "actual": actual, "status": status})
