from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.risk.kill_switch_classifier import classify_kill_switch


NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_file_returns_missing(tmp_path: Path) -> None:
    result = classify_kill_switch(tmp_path / "missing.json", now=NOW).to_dict()

    assert result["status"] == "missing"
    assert result["label"] == "AUSENTE"
    assert result["active_now"] is False
    assert result["blocks_paper"] is False
    assert result["blocks_live"] is False


def test_enabled_false_returns_inactive(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    _write(path, {"enabled": False, "reason": "resolved", "created_at": "2026-05-29T11:00:00Z"})

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "inactive"
    assert result["label"] == "INATIVO"
    assert result["active_now"] is False
    assert result["age_minutes"] == 60.0


def test_enabled_true_without_expires_at_returns_active(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    _write(path, {"enabled": True, "reason": "manual halt", "created_at": "2026-05-29T11:00:00Z"})

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "active"
    assert result["label"] == "ATIVO"
    assert result["active_now"] is True
    assert result["blocks_paper"] is True
    assert result["blocks_live"] is True


def test_enabled_true_with_future_expires_at_returns_active(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    _write(
        path,
        {
            "enabled": True,
            "reason": "temporary halt",
            "created_at": "2026-05-29T11:00:00Z",
            "expires_at": "2026-05-29T13:00:00Z",
        },
    )

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "active"
    assert result["active_now"] is True
    assert result["blocks_paper"] is True
    assert result["blocks_live"] is True


def test_enabled_true_with_past_expires_at_returns_expired(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    _write(
        path,
        {
            "enabled": True,
            "reason": "old halt",
            "created_at": "2026-05-29T10:00:00Z",
            "expires_at": "2026-05-29T11:00:00Z",
        },
    )

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "expired"
    assert result["label"] == "EXPIRADO"
    assert result["active_now"] is False
    assert result["blocks_paper"] is False
    assert result["blocks_live"] is False


def test_invalid_json_returns_conservative_invalid(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    path.write_text("{not-json", encoding="utf-8")

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "invalid"
    assert result["label"] == "INVÁLIDO"
    assert result["active_now"] is True
    assert result["blocks_paper"] is True
    assert result["blocks_live"] is True
    assert result["parse_error"]


def test_guard_state_shape_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch_guard.json"
    _write(
        path,
        {
            "global": {
                "enabled": True,
                "reason": "global halt",
                "updated_at": "2026-05-29T11:30:00Z",
            },
            "symbols": {},
        },
    )

    result = classify_kill_switch(path, now=NOW).to_dict()

    assert result["status"] == "active"
    assert result["created_at"] == "2026-05-29T11:30:00Z"
    assert result["age_minutes"] == 30.0


def test_dashboard_integration_is_read_only_and_does_not_trade() -> None:
    dashboard = Path("smartcrypto/dashboard/app.py").read_text(encoding="utf-8")

    assert "classify_kill_switch" in dashboard
    assert "set_kill_switch" not in dashboard
    assert "activate_global" not in dashboard
    assert "clear_global" not in dashboard
    assert "create_order(" not in dashboard
    assert "fetch_balance(" not in dashboard


def test_classifier_does_not_modify_kill_switch_file(tmp_path: Path) -> None:
    path = tmp_path / "kill_switch.json"
    payload = {"enabled": True, "reason": "manual halt", "created_at": "2026-05-29T11:00:00Z"}
    _write(path, payload)
    before = path.read_text(encoding="utf-8")

    classify_kill_switch(path, now=NOW)

    assert path.read_text(encoding="utf-8") == before
