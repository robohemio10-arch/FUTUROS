from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_RUNTIME_MODES = {"paper", "research", "shadow"}
MODEL_STATUSES = {"CANDIDATE", "APPROVED_FOR_SHADOW", "REJECTED", "ROLLED_BACK"}
VALID_RISK_STATUSES = {"PASSED", "APPROVED", "APPROVED_FOR_SHADOW", "SHADOW_ONLY", "OK"}
TRUE_VALUES = {"1", "true", "yes", "y", "on"}


class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    model_version: str
    feature_contract_version: str | None
    training_dataset_hash: str | None
    status: str
    risk_status: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    def __init__(self, path: str | Path, *, runtime_mode: str = "research") -> None:
        assert_safe_runtime(runtime_mode)
        self.path = Path(path)
        self.runtime_mode = str(runtime_mode).strip().lower()

    def register(
        self,
        *,
        model_id: str,
        model_version: str,
        status: str = "CANDIDATE",
        feature_contract_version: str | None = None,
        training_dataset_hash: str | None = None,
        risk_status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> ModelRecord:
        normalized_status = normalize_status(status)
        validate_shadow_approval(
            normalized_status,
            feature_contract_version=feature_contract_version,
            risk_status=risk_status,
        )
        record = ModelRecord(
            model_id=require_text(model_id, "model_id"),
            model_version=require_text(model_version, "model_version"),
            feature_contract_version=normalize_optional_text(feature_contract_version),
            training_dataset_hash=normalize_optional_text(training_dataset_hash),
            status=normalized_status,
            risk_status=str(risk_status or "PENDING"),
            created_at=utc_timestamp(),
            metadata=dict(metadata or {}),
        )
        state = self._load()
        state["models"].append(record.to_dict())
        self._save(state)
        return record

    def update_status(
        self,
        model_id: str,
        model_version: str,
        status: str,
        *,
        risk_status: str | None = None,
        reason: str | None = None,
    ) -> ModelRecord:
        normalized_status = normalize_status(status)
        state = self._load()
        record = self._find(state, model_id, model_version)
        next_risk_status = str(risk_status or record.get("risk_status") or "PENDING")
        validate_shadow_approval(
            normalized_status,
            feature_contract_version=record.get("feature_contract_version"),
            risk_status=next_risk_status,
        )
        record["status"] = normalized_status
        record["risk_status"] = next_risk_status
        record.setdefault("metadata", {})
        if reason:
            record["metadata"]["status_reason"] = reason
        self._save(state)
        return ModelRecord(**record)

    def rollback(
        self,
        model_id: str,
        model_version: str,
        *,
        reason: str = "metadata_rollback",
    ) -> ModelRecord:
        return self.update_status(
            model_id,
            model_version,
            "ROLLED_BACK",
            reason=reason,
        )

    def list_models(self) -> list[ModelRecord]:
        return [ModelRecord(**record) for record in self._load()["models"]]

    def _find(
        self,
        state: dict[str, Any],
        model_id: str,
        model_version: str,
    ) -> dict[str, Any]:
        for record in state["models"]:
            if (
                record.get("model_id") == model_id
                and record.get("model_version") == model_version
            ):
                return record
        raise ModelRegistryError(f"model_not_found:{model_id}:{model_version}")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "models": []}
        payload = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ModelRegistryError("model_registry_root_must_be_mapping")
        models = payload.setdefault("models", [])
        if not isinstance(models, list):
            raise ModelRegistryError("model_registry_models_must_be_list")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return output_path


def registry_payload(
    *,
    status: str,
    model_name: str,
    model_path: str | None,
    training_report_path: str | None,
    walk_forward_report_path: str | None,
    production_enabled: bool,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "model_name": model_name,
        "model_path": model_path,
        "training_report_path": training_report_path,
        "walk_forward_report_path": walk_forward_report_path,
        "production_enabled": production_enabled,
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_status(status: str) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == "LIVE":
        raise ModelRegistryError("live_model_promotion_forbidden")
    if normalized not in MODEL_STATUSES:
        raise ModelRegistryError(f"invalid_model_status:{status}")
    return normalized


def validate_shadow_approval(
    status: str,
    *,
    feature_contract_version: str | None,
    risk_status: str,
) -> None:
    if status != "APPROVED_FOR_SHADOW":
        return
    if not normalize_optional_text(feature_contract_version):
        raise ModelRegistryError("feature_contract_version_required_for_shadow_approval")
    if str(risk_status or "").strip().upper() not in VALID_RISK_STATUSES:
        raise ModelRegistryError(f"risk_status_invalid_for_shadow_approval:{risk_status}")


def assert_safe_runtime(runtime_mode: str) -> None:
    normalized = str(runtime_mode or "").strip().lower()
    reasons: list[str] = []
    if normalized not in SAFE_RUNTIME_MODES:
        reasons.append(f"runtime_mode_not_allowed:{runtime_mode}")
    for name in (
        "LIVE_ENABLED",
        "ORDER_SUBMISSION_ENABLED",
        "REAL_ORDER_SUBMISSION_ENABLED",
    ):
        if str(os.getenv(name, "")).strip().lower() in TRUE_VALUES:
            reasons.append(f"{name}=true")
    if reasons:
        raise ModelRegistryError("unsafe_model_registry_runtime:" + ",".join(reasons))


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelRegistryError(f"{field_name}_required")
    return text


def normalize_optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
