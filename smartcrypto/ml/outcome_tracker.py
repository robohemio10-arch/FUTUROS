from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OutcomeTrackerError(ValueError):
    pass


@dataclass(frozen=True)
class OutcomeRecord:
    decision_id: str
    trade_id: str | None
    target_win: bool
    return_pct: float
    pnl: float | None
    resolved_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OutcomeTracker:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def record_outcome(
        self,
        *,
        decision_id: str,
        target_win: bool,
        return_pct: float,
        trade_id: str | None = None,
        pnl: float | None = None,
        resolved_at: str | None = None,
    ) -> OutcomeRecord:
        record = OutcomeRecord(
            decision_id=require_text(decision_id, "decision_id"),
            trade_id=normalize_optional_text(trade_id),
            target_win=bool(target_win),
            return_pct=float(return_pct),
            pnl=None if pnl is None else float(pnl),
            resolved_at=resolved_at or utc_timestamp(),
        )
        payload = self._load()
        payload["outcomes"].append(record.to_dict())
        self._save(payload)
        return record

    def metrics(self) -> dict[str, Any]:
        outcomes = self._load()["outcomes"]
        total = len(outcomes)
        resolved = [item for item in outcomes if item.get("resolved_at")]
        win_count = sum(1 for item in resolved if item.get("target_win") is True)
        returns = [float(item.get("return_pct", 0.0)) for item in resolved]
        return {
            "total_decisions": total,
            "resolved_decisions": len(resolved),
            "win_rate": win_count / len(resolved) if resolved else 0.0,
            "average_return_pct": sum(returns) / len(returns) if returns else 0.0,
        }

    def list_outcomes(self) -> list[OutcomeRecord]:
        return [OutcomeRecord(**item) for item in self._load()["outcomes"]]

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "outcomes": []}
        payload = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            raise OutcomeTrackerError("outcome_tracker_root_must_be_mapping")
        payload.setdefault("schema_version", 1)
        payload.setdefault("outcomes", [])
        if not isinstance(payload["outcomes"], list):
            raise OutcomeTrackerError("outcomes_must_be_list")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def require_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OutcomeTrackerError(f"{field_name}_required")
    return text


def normalize_optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
