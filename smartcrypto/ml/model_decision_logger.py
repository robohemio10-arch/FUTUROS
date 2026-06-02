from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


RISK_ACTIONS = {"ALLOW_SHADOW", "BLOCK_AI", "REDUCE_CONFIDENCE", "NO_ACTION"}
SECRET_MARKERS = ("secret", "token", "key", "password", "credential")
DEFAULT_OUTPUT_PATH = Path("data/reports/ai_shadow_model_decisions.jsonl")
DEFAULT_REPORT_PATH = Path("data/reports/ai_shadow_model_decision_logger_report.json")
DEFAULT_REGISTRY_PATH = Path("data/models/registry/model_registry.json")
DEFAULT_TRAINER_REPORT_PATH = Path("data/reports/ai_shadow_incremental_trainer_report.json")


class ModelDecisionLoggerError(ValueError):
    pass


@dataclass(frozen=True)
class ModelDecision:
    decision_id: str
    timestamp_utc: str
    model_id: str
    model_version: str
    feature_contract_version: str
    symbol: str
    score: float
    confidence: float
    drift_status: str
    risk_action: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelDecisionLogger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record(
        self,
        *,
        model_id: str,
        model_version: str,
        feature_contract_version: str,
        symbol: str,
        score: float,
        confidence: float,
        drift_status: str,
        risk_action: str,
        payload: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> ModelDecision:
        action = str(risk_action or "").strip().upper()
        if action not in RISK_ACTIONS:
            raise ModelDecisionLoggerError(f"invalid_risk_action:{risk_action}")
        decision = ModelDecision(
            decision_id=decision_id or str(uuid.uuid4()),
            timestamp_utc=utc_timestamp(),
            model_id=require_text(model_id, "model_id"),
            model_version=require_text(model_version, "model_version"),
            feature_contract_version=require_text(
                feature_contract_version,
                "feature_contract_version",
            ),
            symbol=require_text(symbol, "symbol").upper(),
            score=float(score),
            confidence=float(confidence),
            drift_status=str(drift_status or "UNKNOWN").strip().upper(),
            risk_action=action,
            payload=sanitize_payload(payload or {}),
        )
        self.append(decision)
        return decision

    def append(self, decision: ModelDecision) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def read(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)


def log_ai_shadow_model_decisions(
    *,
    input_path: str | Path,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    registry_path: str | Path | None = DEFAULT_REGISTRY_PATH,
    trainer_report_path: str | Path | None = DEFAULT_TRAINER_REPORT_PATH,
    strict: bool = False,
) -> dict[str, Any]:
    source = Path(input_path)
    output = Path(output_path)
    report_file = Path(report_path)
    if not source.exists():
        return blocked_report(
            reason="missing_input",
            input_path=source,
            output_path=output,
            report_path=report_file,
            errors=[f"missing_input:{source}"],
        )

    model_context = load_model_context(registry_path=registry_path, trainer_report_path=trainer_report_path)
    rows = read_records(source)
    if not rows:
        return blocked_report(
            reason="empty_input",
            input_path=source,
            output_path=output,
            report_path=report_file,
            errors=["empty_input"],
        )

    violations = safety_violations(model_context)
    for row in rows:
        violations.extend(safety_violations(row))
    if strict and violations:
        return blocked_report(
            reason="unsafe_safety_flags",
            input_path=source,
            output_path=output,
            report_path=report_file,
            errors=sorted(set(violations)),
        )

    decisions: list[dict[str, Any]] = []
    for row in rows:
        merged = {**model_context, **row}
        model_id = str(merged.get("model_id") or "").strip()
        model_version = str(merged.get("model_version") or "").strip()
        if not model_id or not model_version:
            return blocked_report(
                reason="missing_model_identity",
                input_path=source,
                output_path=output,
                report_path=report_file,
                errors=["missing_model_id_or_model_version"],
            )
        hard_violations = hard_blocking_safety_violations(merged)
        if hard_violations:
            return blocked_report(
                reason="unsafe_safety_flags",
                input_path=source,
                output_path=output,
                report_path=report_file,
                errors=hard_violations,
            )
        decisions.append(normalize_decision_row(merged, source=str(source)))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        for decision in decisions:
            handle.write(json.dumps(decision, ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")

    report = {
        "status": "ok",
        "reason": "ok",
        "input_path": str(source),
        "output_path": str(output),
        "report_path": str(report_file),
        "rows_input": int(len(rows)),
        "logged_rows": int(len(decisions)),
        "append_only": True,
        "strict": bool(strict),
        "model_id": decisions[0]["model_id"] if decisions else None,
        "model_version": decisions[0]["model_version"] if decisions else None,
        "sends_orders": False,
        "changes_risk": False,
        "created_at_utc": utc_timestamp(),
        **safety_payload(),
    }
    write_json(report_file, report)
    return report


def normalize_decision_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    threshold = first_float(row, ("threshold", "probability_threshold", "decision_threshold"), default=0.5)
    probability = first_float(row, ("probability", "probability_win", "win_probability", "score", "prediction"), default=0.0)
    prediction = row.get("prediction", row.get("decision", row.get("action_shadow", probability)))
    action = str(row.get("action_shadow") or row.get("decision") or "").strip().upper()
    if not action:
        action = "SHADOW_ENTRY" if probability >= threshold else "SHADOW_SKIP"
    decision_id = str(row.get("decision_id") or stable_hash({"row": row, "probability": probability, "threshold": threshold})[:24])
    correlation_id = str(row.get("correlation_id") or row.get("order_id") or row.get("trade_id") or decision_id)
    feature_columns = row.get("feature_columns") if isinstance(row.get("feature_columns"), list) else []
    feature_payload = {key: value for key, value in row.items() if str(key).startswith("feature_")}
    return {
        "decision_id": decision_id,
        "correlation_id": correlation_id,
        "model_id": str(row.get("model_id") or "").strip(),
        "model_version": str(row.get("model_version") or "").strip(),
        "registry_status": str(row.get("registry_status") or row.get("status") or "unknown"),
        "promotion_status": str(row.get("promotion_status") or "pending"),
        "symbol": normalize_symbol(row.get("symbol") or row.get("moeda") or row.get("pair")),
        "side": normalize_side(row.get("side") or row.get("fechar_side")),
        "prediction": prediction,
        "probability": probability,
        "confidence": first_float(row, ("confidence",), default=abs(probability - 0.5) * 2),
        "threshold": threshold,
        "action_shadow": action,
        "reason": str(row.get("reason") or row.get("decision_reason") or "shadow_model_decision_logged"),
        "feature_columns_count": int(row.get("feature_columns_count") or row.get("feature_count") or len(feature_columns) or len(feature_payload)),
        "feature_hash": stable_hash(feature_columns or feature_payload),
        "input_row_hash": stable_hash(sanitize_payload(row)),
        "decided_at_utc": stringify_timestamp(row.get("decided_at_utc") or row.get("created_at") or row.get("open_time_utc")) or utc_timestamp(),
        "source": str(row.get("source") or source),
        "order_id": normalize_optional_text(row.get("order_id") or row.get("trade_id")),
        "open_time_utc": stringify_timestamp(row.get("open_time_utc") or row.get("timestamp") or row.get("open_1m_ts")),
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def load_model_context(
    *,
    registry_path: str | Path | None,
    trainer_report_path: str | Path | None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if registry_path:
        registry = Path(registry_path)
        if registry.exists():
            payload = json.loads(registry.read_text(encoding="utf-8") or "{}")
            challenger = latest_challenger(payload)
            if challenger:
                context.update(challenger)
                context["registry_status"] = challenger.get("promotion_gate_status", "challenger")
    if trainer_report_path:
        trainer = Path(trainer_report_path)
        if trainer.exists():
            payload = json.loads(trainer.read_text(encoding="utf-8") or "{}")
            context.update({key: value for key, value in payload.items() if value is not None})
            context.setdefault("registry_status", "trainer_report")
    return context


def latest_challenger(registry: dict[str, Any]) -> dict[str, Any] | None:
    challengers = registry.get("challengers")
    if isinstance(challengers, list) and challengers:
        return challengers[-1]
    return None


def read_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return read_jsonl(path)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("decisions", "rows", "records"):
                if isinstance(payload.get(key), list):
                    return [dict(item) for item in payload[key] if isinstance(item, dict)]
            return [payload]
    if suffix == ".parquet":
        return pd.read_parquet(path).to_dict("records")
    if suffix == ".csv":
        return pd.read_csv(path).to_dict("records")
    raise ModelDecisionLoggerError(f"unsupported_input_extension:{path.suffix}")


def blocked_report(
    *,
    reason: str,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    errors: list[str],
) -> dict[str, Any]:
    report = {
        "status": "blocked",
        "reason": reason,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "report_path": str(report_path),
        "rows_input": 0,
        "logged_rows": 0,
        "blocking_errors": errors,
        "append_only": True,
        "sends_orders": False,
        "changes_risk": False,
        "created_at_utc": utc_timestamp(),
        **safety_payload(),
    }
    write_json(report_path, report)
    return report


def safety_violations(payload: dict[str, Any]) -> list[str]:
    violations = []
    if payload.get("paper_only") is False:
        violations.append("paper_only_not_true")
    if payload.get("shadow_only") is False:
        violations.append("shadow_only_not_true")
    violations.extend(hard_blocking_safety_violations(payload))
    return violations


def hard_blocking_safety_violations(payload: dict[str, Any]) -> list[str]:
    violations = []
    for key in ("live_trading_enabled", "order_submission_enabled", "real_order_submission_enabled", "exchange_private_access", "sends_orders", "changes_risk"):
        if payload.get(key) is True:
            violations.append(f"unsafe_flag:{key}=true")
    return violations


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(marker in key_text.lower() for marker in SECRET_MARKERS):
                continue
            clean[key_text] = sanitize_payload(item)
        return clean
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def first_float(row: dict[str, Any], keys: tuple[str, ...], *, default: float) -> float:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except TypeError:
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)


def normalize_symbol(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).upper().replace("/USDT:USDT", "USDT").replace("/", "").replace(":", "").strip()


def normalize_side(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().lower()
    if "short" in text or "sell" in text:
        return "short"
    if "long" in text or "buy" in text:
        return "long"
    return text


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip()
    return text or None


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelDecisionLoggerError(f"{field_name}_required")
    return text


def stable_hash(value: Any) -> str:
    material = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def stringify_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return str(value)
    return parsed.isoformat()


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


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
