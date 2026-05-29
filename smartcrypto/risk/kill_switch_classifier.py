from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_LABELS = {
    "missing": "AUSENTE",
    "inactive": "INATIVO",
    "active": "ATIVO",
    "expired": "EXPIRADO",
    "historical": "HISTÓRICO",
    "invalid": "INVÁLIDO",
}


@dataclass(frozen=True)
class KillSwitchClassification:
    status: str
    active_now: bool
    blocks_paper: bool
    blocks_live: bool
    reason: str | None
    created_at: str | None
    expires_at: str | None
    age_minutes: float | None
    source_path: str
    parse_error: str | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["label"] = self.label or STATUS_LABELS.get(self.status, self.status.upper())
        return payload


def classify_kill_switch(
    path: str | Path = "data/runtime/kill_switch.json",
    *,
    now: datetime | None = None,
    invalid_blocks_paper: bool = True,
) -> KillSwitchClassification:
    current = _normalize_now(now)
    target = Path(path)
    if not target.exists():
        return KillSwitchClassification(
            status="missing",
            active_now=False,
            blocks_paper=False,
            blocks_live=False,
            reason="kill_switch_file_missing",
            created_at=None,
            expires_at=None,
            age_minutes=None,
            source_path=str(target),
        )

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return KillSwitchClassification(
            status="invalid",
            active_now=True,
            blocks_paper=bool(invalid_blocks_paper),
            blocks_live=True,
            reason="kill_switch_json_invalid",
            created_at=None,
            expires_at=None,
            age_minutes=None,
            source_path=str(target),
            parse_error=str(exc),
        )

    if not isinstance(payload, dict):
        return KillSwitchClassification(
            status="invalid",
            active_now=True,
            blocks_paper=bool(invalid_blocks_paper),
            blocks_live=True,
            reason="kill_switch_root_not_object",
            created_at=None,
            expires_at=None,
            age_minutes=None,
            source_path=str(target),
            parse_error="root_not_object",
        )

    entry = _select_global_entry(payload)
    enabled = entry.get("enabled")
    if enabled is not True:
        created_at = _first_text(entry, "created_at", "updated_at", "timestamp_utc")
        return KillSwitchClassification(
            status="inactive",
            active_now=False,
            blocks_paper=False,
            blocks_live=False,
            reason=str(entry.get("reason") or "kill_switch_disabled"),
            created_at=created_at,
            expires_at=_first_text(entry, "expires_at", "expires_on"),
            age_minutes=_age_minutes(created_at, current),
            source_path=str(target),
        )

    created_at = _first_text(entry, "created_at", "updated_at", "timestamp_utc")
    expires_at = _first_text(entry, "expires_at", "expires_on")
    expires_ts = _parse_timestamp(expires_at)
    if expires_at and expires_ts is None:
        return KillSwitchClassification(
            status="invalid",
            active_now=True,
            blocks_paper=bool(invalid_blocks_paper),
            blocks_live=True,
            reason="kill_switch_expires_at_invalid",
            created_at=created_at,
            expires_at=expires_at,
            age_minutes=_age_minutes(created_at, current),
            source_path=str(target),
            parse_error=f"invalid_expires_at:{expires_at}",
        )

    if expires_ts is not None and expires_ts <= current:
        return KillSwitchClassification(
            status="expired",
            active_now=False,
            blocks_paper=False,
            blocks_live=False,
            reason=str(entry.get("reason") or "kill_switch_expired"),
            created_at=created_at,
            expires_at=expires_at,
            age_minutes=_age_minutes(created_at, current),
            source_path=str(target),
        )

    return KillSwitchClassification(
        status="active",
        active_now=True,
        blocks_paper=True,
        blocks_live=True,
        reason=str(entry.get("reason") or "kill_switch_enabled"),
        created_at=created_at,
        expires_at=expires_at,
        age_minutes=_age_minutes(created_at, current),
        source_path=str(target),
    )


def _select_global_entry(payload: dict[str, Any]) -> dict[str, Any]:
    global_entry = payload.get("global")
    if isinstance(global_entry, dict):
        return global_entry
    return payload


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normalize_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc) if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_minutes(value: str | None, now: datetime) -> float | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return float(max(0.0, (now - parsed).total_seconds() / 60.0))
