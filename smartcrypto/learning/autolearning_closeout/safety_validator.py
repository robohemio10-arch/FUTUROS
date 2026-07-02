"""Safety validation for read-only auto-learning closeout evidence."""

from __future__ import annotations

from typing import Any


REQUIRED_FALSE_FLAGS = (
    "operational_authority",
    "promotion_allowed",
    "promotion_eligible",
    "registry_write_performed",
    "veto_registry_write_performed",
    "model_promotion_performed",
    "active_model_changed",
    "qlib_runtime_updated",
    "ai_shadow_runtime_updated",
    "veto_runtime_active",
    "ai_shadow_veto_runtime_active",
    "sends_orders",
    "exchange_private_access",
    "changes_risk",
    "writes_runtime",
    "writes_sqlite",
    "live_release_allowed",
    "canary_release_allowed",
    "order_submission_enabled",
    "real_order_submission_enabled",
)

REQUIRED_TRUE_FLAGS = ("paper_only", "shadow_only")


def validate_safety(payloads: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for stage_id, payload in payloads.items():
        for flag in REQUIRED_FALSE_FLAGS:
            value = read_flag(payload, flag, default=False)
            rows.append(
                {
                    "stage_id": stage_id,
                    "flag": flag,
                    "expected": False,
                    "actual": bool(value),
                    "status": "ok" if bool(value) is False else "blocked",
                }
            )
        for flag in REQUIRED_TRUE_FLAGS:
            value = read_flag(payload, flag, default=True)
            rows.append(
                {
                    "stage_id": stage_id,
                    "flag": flag,
                    "expected": True,
                    "actual": bool(value),
                    "status": "ok" if bool(value) is True else "blocked",
                }
            )
    violations = [row for row in rows if row["status"] == "blocked"]
    return {"safety_status": "blocked" if violations else "ok", "safety_matrix": rows, "safety_violations": violations}


def read_flag(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    if key in payload:
        return bool(payload[key])
    nested = payload.get("safety_flags")
    if isinstance(nested, dict) and key in nested:
        return bool(nested[key])
    return default


def closeout_safety_flags() -> dict[str, bool]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "operational_authority": False,
        "paper_observation_allowed": False,
        "ready_for_shadow_observation": False,
        "promotion_eligible": False,
        "promotion_allowed": False,
        "registry_write_performed": False,
        "veto_registry_write_performed": False,
        "model_promotion_performed": False,
        "active_model_changed": False,
        "qlib_training_performed": False,
        "ai_shadow_training_performed": False,
        "ai_shadow_challenger_training_performed": False,
        "training_performed": False,
        "qlib_runtime_updated": False,
        "ai_shadow_runtime_updated": False,
        "ai_shadow_veto_runtime_active": False,
        "veto_runtime_active": False,
        "sends_orders": False,
        "exchange_private_access": False,
        "changes_risk": False,
        "writes_runtime": False,
        "writes_sqlite": False,
        "writes_parquet": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
    }
