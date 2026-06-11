from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smartcrypto.ops.dashboard_snapshots.build_context import create_dashboard_build_context
from smartcrypto.ops.dashboard_snapshots.contracts import validate_dashboard_snapshot


NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def write_json(root: Path, relative: str, payload: Any) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def write_jsonl(root: Path, relative: str, rows: list[dict[str, Any]]) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return target


def context(root: Path, *, strict: bool = False, writes: bool = False):
    return create_dashboard_build_context(
        root,
        output_dir=root / "output",
        now_utc=NOW,
        runtime_mode="paper",
        strict=strict,
        allow_writes_to_output_dir=writes,
    )


def assert_safe_snapshot(snapshot: dict[str, Any], schema_version: str, sections: tuple[str, ...]) -> None:
    assert snapshot["schema_version"] == schema_version
    assert snapshot["runtime_mode"] == "paper"
    assert snapshot["dashboard_readonly"] is True
    assert snapshot["paper_only"] is True
    assert snapshot["shadow_only"] is True
    assert snapshot["live_locked"] is True
    assert snapshot["order_submission_enabled"] is False
    assert snapshot["real_order_submission_enabled"] is False
    assert set(sections).issubset(snapshot["sections"])
    assert validate_dashboard_snapshot(snapshot) == []
    audit = snapshot["audit"]
    assert audit["uses_private_exchange"] is False
    assert audit["uses_ccxt"] is False
    assert audit["sends_orders"] is False
    assert audit["changes_risk"] is False
    assert audit["promotes_model"] is False
    assert audit["changes_model"] is False
    assert audit["changes_active_signals"] is False
    json.dumps(snapshot)
