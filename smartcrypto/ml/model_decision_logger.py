from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RISK_ACTIONS = {"ALLOW_SHADOW", "BLOCK_AI", "REDUCE_CONFIDENCE", "NO_ACTION"}
SECRET_MARKERS = ("secret", "token", "key", "password", "credential")


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
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


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


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelDecisionLoggerError(f"{field_name}_required")
    return text


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
