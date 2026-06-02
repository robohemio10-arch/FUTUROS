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

DEFAULT_TRAINER_REPORT_PATH = Path("data/reports/ai_shadow_incremental_trainer_report.json")
DEFAULT_REGISTRY_PATH = Path("data/models/registry/model_registry.json")
DEFAULT_GATE_REPORT_PATH = Path("data/reports/model_registry_promotion_gate_report.json")
REGISTRY_VERSION = 2


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


@dataclass(frozen=True)
class PromotionGateConfig:
    min_rows: int = 100
    min_accuracy: float = 0.50
    min_f1: float = 0.50
    min_roc_auc: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_rows": int(self.min_rows),
            "min_accuracy": float(self.min_accuracy),
            "min_f1": float(self.min_f1),
            "min_roc_auc": None if self.min_roc_auc is None else float(self.min_roc_auc),
        }


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
        state.setdefault("models", []).append(record.to_dict())
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
        return [ModelRecord(**record) for record in self._load().get("models", [])]

    def _find(
        self,
        state: dict[str, Any],
        model_id: str,
        model_version: str,
    ) -> dict[str, Any]:
        for record in state.get("models", []):
            if (
                record.get("model_id") == model_id
                and record.get("model_version") == model_version
            ):
                return record
        raise ModelRegistryError(f"model_not_found:{model_id}:{model_version}")

    def _load(self) -> dict[str, Any]:
        return load_registry(self.path)

    def _save(self, payload: dict[str, Any]) -> None:
        save_registry(self.path, payload)


def register_ai_shadow_challenger_model(
    *,
    trainer_report_path: str | Path = DEFAULT_TRAINER_REPORT_PATH,
    registry_path: str | Path = DEFAULT_REGISTRY_PATH,
    report_path: str | Path = DEFAULT_GATE_REPORT_PATH,
    min_rows: int = 100,
    min_accuracy: float = 0.50,
    min_f1: float = 0.50,
    min_roc_auc: float | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    trainer_report = Path(trainer_report_path)
    registry_file = Path(registry_path)
    gate_report_file = Path(report_path)
    config = PromotionGateConfig(
        min_rows=int(min_rows),
        min_accuracy=float(min_accuracy),
        min_f1=float(min_f1),
        min_roc_auc=min_roc_auc,
    )

    if not trainer_report.exists():
        report = gate_report(
            status="blocked",
            reason="missing_trainer_report",
            trainer_report_path=trainer_report,
            registry_path=registry_file,
            report_path=gate_report_file,
            config=config,
            promotion_violations=[f"missing_trainer_report:{trainer_report}"],
        )
        write_json(gate_report_file, report)
        return report

    metadata = json.loads(trainer_report.read_text(encoding="utf-8") or "{}")
    structural_violations = validate_metadata_structure(metadata)
    safety_violations = validate_safety_flags(metadata)
    if structural_violations or safety_violations:
        violations = [*structural_violations, *safety_violations]
        report = gate_report(
            status="blocked",
            reason="invalid_trainer_metadata",
            trainer_report_path=trainer_report,
            registry_path=registry_file,
            report_path=gate_report_file,
            config=config,
            metadata=metadata,
            promotion_violations=violations,
        )
        write_json(gate_report_file, report)
        return report

    promotion_violations = evaluate_promotion_gate(metadata, config)
    if strict and promotion_violations:
        report = gate_report(
            status="blocked",
            reason="promotion_gate_blocked",
            trainer_report_path=trainer_report,
            registry_path=registry_file,
            report_path=gate_report_file,
            config=config,
            metadata=metadata,
            promotion_violations=promotion_violations,
        )
        write_json(gate_report_file, report)
        return report

    state = load_registry(registry_file)
    challenger = challenger_record(
        metadata,
        trainer_report_path=trainer_report,
        promotion_violations=promotion_violations,
    )
    upsert_challenger(state, challenger)
    if promotion_violations:
        append_rejected_promotion(state, challenger, promotion_violations)
    state["updated_at_utc"] = utc_timestamp()
    state.update(safety_payload())
    save_registry(registry_file, state)

    report = gate_report(
        status="ok",
        reason="challenger_registered_pending",
        trainer_report_path=trainer_report,
        registry_path=registry_file,
        report_path=gate_report_file,
        config=config,
        metadata=metadata,
        challenger=challenger,
        promotion_violations=promotion_violations,
        challenger_registered=True,
    )
    write_json(gate_report_file, report)
    return report


def validate_metadata_structure(metadata: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    if not str(metadata.get("model_id") or "").strip():
        violations.append("missing_model_id")
    if not str(metadata.get("model_version") or "").strip():
        violations.append("missing_model_version")
    feature_columns = metadata.get("feature_columns")
    if not isinstance(feature_columns, list) or not feature_columns:
        violations.append("missing_feature_columns")
    class_balance = metadata.get("class_balance")
    if not isinstance(class_balance, dict) or not class_balance:
        violations.append("missing_class_balance")
    metrics = metadata.get("metrics")
    if not isinstance(metrics, dict):
        violations.append("missing_metrics")
    return violations


def validate_safety_flags(metadata: dict[str, Any]) -> list[str]:
    expected = {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
    }
    violations: list[str] = []
    for key, expected_value in expected.items():
        if metadata.get(key) is not expected_value:
            violations.append(f"unsafe_safety_flag:{key}={metadata.get(key)!r}")
    return violations


def evaluate_promotion_gate(metadata: dict[str, Any], config: PromotionGateConfig) -> list[str]:
    violations: list[str] = []
    if bool(metadata.get("auto_promote")):
        violations.append("auto_promotion_forbidden")
    if bool(metadata.get("sample_warning")):
        violations.append("sample_warning_true")
    input_rows = int(metadata.get("input_rows") or 0)
    if input_rows < int(config.min_rows):
        violations.append(f"input_rows_below_minimum:{input_rows}<{int(config.min_rows)}")
    if is_single_class(metadata.get("class_balance")):
        violations.append("single_target_class")

    metrics = metadata.get("metrics") if isinstance(metadata.get("metrics"), dict) else {}
    accuracy = metric_value(metrics, "accuracy")
    f1 = metric_value(metrics, "f1")
    roc_auc = metric_value(metrics, "roc_auc")
    if accuracy is None or accuracy < float(config.min_accuracy):
        violations.append(f"accuracy_below_minimum:{accuracy}<{float(config.min_accuracy)}")
    if f1 is None or f1 < float(config.min_f1):
        violations.append(f"f1_below_minimum:{f1}<{float(config.min_f1)}")
    if config.min_roc_auc is not None and (roc_auc is None or roc_auc < float(config.min_roc_auc)):
        violations.append(f"roc_auc_below_minimum:{roc_auc}<{float(config.min_roc_auc)}")
    return violations


def metric_value(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_single_class(class_balance: Any) -> bool:
    if not isinstance(class_balance, dict):
        return True
    nonzero = [value for value in class_balance.values() if int(value or 0) > 0]
    return len(nonzero) < 2


def challenger_record(
    metadata: dict[str, Any],
    *,
    trainer_report_path: Path,
    promotion_violations: list[str],
) -> dict[str, Any]:
    promotion_gate_status = "blocked" if promotion_violations else "eligible_pending_manual_review"
    return {
        "model_id": metadata["model_id"],
        "model_version": metadata["model_version"],
        "registered_at_utc": utc_timestamp(),
        "trainer_report_path": str(trainer_report_path),
        "model_path": metadata.get("model_path"),
        "metadata_path": metadata.get("metadata_path"),
        "input_path": metadata.get("input_path"),
        "input_rows": int(metadata.get("input_rows") or 0),
        "feature_columns": list(metadata.get("feature_columns") or []),
        "target_column": metadata.get("target_column"),
        "class_balance": dict(metadata.get("class_balance") or {}),
        "metrics": dict(metadata.get("metrics") or {}),
        "promotion_status": "pending",
        "promotion_gate_status": promotion_gate_status,
        "promotion_violations": promotion_violations,
        "auto_promote": False,
        **safety_payload(),
    }


def upsert_challenger(state: dict[str, Any], challenger: dict[str, Any]) -> None:
    challengers = state.setdefault("challengers", [])
    for index, current in enumerate(challengers):
        if (
            current.get("model_id") == challenger["model_id"]
            and current.get("model_version") == challenger["model_version"]
        ):
            challengers[index] = challenger
            return
    challengers.append(challenger)


def append_rejected_promotion(
    state: dict[str, Any],
    challenger: dict[str, Any],
    violations: list[str],
) -> None:
    state.setdefault("rejected_promotions", []).append(
        {
            "model_id": challenger["model_id"],
            "model_version": challenger["model_version"],
            "promotion_status": "pending",
            "promotion_gate_status": "blocked",
            "violations": violations,
            "created_at_utc": utc_timestamp(),
        }
    )


def gate_report(
    *,
    status: str,
    reason: str,
    trainer_report_path: Path,
    registry_path: Path,
    report_path: Path,
    config: PromotionGateConfig,
    metadata: dict[str, Any] | None = None,
    challenger: dict[str, Any] | None = None,
    promotion_violations: list[str] | None = None,
    challenger_registered: bool = False,
) -> dict[str, Any]:
    metadata = metadata or {}
    violations = promotion_violations or []
    return {
        "status": status,
        "reason": reason,
        "trainer_report_path": str(trainer_report_path),
        "registry_path": str(registry_path),
        "report_path": str(report_path),
        "challenger_registered": bool(challenger_registered),
        "model_id": metadata.get("model_id"),
        "model_version": metadata.get("model_version"),
        "promotion_status": "pending" if metadata.get("model_id") else None,
        "promotion_gate_status": "blocked" if violations else "eligible_pending_manual_review",
        "promotion_violations": violations,
        "auto_promote": False,
        "gate_config": config.to_dict(),
        "challenger": challenger,
        "created_at_utc": utc_timestamp(),
        **safety_payload(),
    }


def load_registry(path: str | Path) -> dict[str, Any]:
    registry_path = Path(path)
    if not registry_path.exists():
        return empty_registry()
    payload = json.loads(registry_path.read_text(encoding="utf-8") or "{}")
    if not isinstance(payload, dict):
        raise ModelRegistryError("model_registry_root_must_be_mapping")
    state = empty_registry()
    state.update(payload)
    if "schema_version" in payload and "registry_version" not in payload:
        state["registry_version"] = REGISTRY_VERSION
    for key in ("models", "challengers", "rejected_promotions"):
        if not isinstance(state.get(key), list):
            raise ModelRegistryError(f"model_registry_{key}_must_be_list")
    return state


def empty_registry() -> dict[str, Any]:
    return {
        "registry_version": REGISTRY_VERSION,
        "updated_at_utc": None,
        "champion_model_id": None,
        "champion_model_version": None,
        "challengers": [],
        "rejected_promotions": [],
        "models": [],
        **safety_payload(),
    }


def save_registry(path: str | Path, payload: dict[str, Any]) -> None:
    registry_path = Path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload.setdefault("registry_version", REGISTRY_VERSION)
    payload.setdefault("champion_model_id", None)
    payload.setdefault("champion_model_version", None)
    payload.setdefault("challengers", [])
    payload.setdefault("rejected_promotions", [])
    payload.setdefault("models", [])
    payload["updated_at_utc"] = payload.get("updated_at_utc") or utc_timestamp()
    payload.update(safety_payload())
    temp_path = registry_path.with_suffix(registry_path.suffix + f".{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    temp_path.replace(registry_path)


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


def safety_payload() -> dict[str, Any]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "runtime_mode": "paper",
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
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
