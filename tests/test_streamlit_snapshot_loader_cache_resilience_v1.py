from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Callable

from smartcrypto.dashboard.services.dashboard_snapshot_service import load_dashboard_snapshot
from smartcrypto.dashboard.services.snapshot_json_loader import (
    SNAPSHOT_CACHE_TTL_SECONDS,
    build_snapshot_byte_reader,
    load_snapshot_json,
)
from tests.dashboard_page_test_support import load_page_module, page_paths, valid_snapshot


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "smartcrypto" / "dashboard" / "services"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_snapshot_returns_controlled_status(tmp_path: Path) -> None:
    result = load_snapshot_json("data/reports/missing.json", project_root=tmp_path)
    snapshot = load_dashboard_snapshot(
        "data/reports/missing.json",
        "expected_v1",
        project_root=tmp_path,
    )

    assert result.status == "MISSING"
    assert result.reason == "file_not_found"
    assert snapshot["status"] == "UNKNOWN"
    assert snapshot["source_status"] == "MISSING"
    assert snapshot["freshness_status"] == "UNKNOWN"
    assert snapshot["last_updated_utc"] is None
    assert snapshot["loader_checked_at_utc"].endswith("Z")


def test_empty_snapshot_returns_invalid_empty(tmp_path: Path) -> None:
    target = tmp_path / "data" / "reports" / "empty.json"
    target.parent.mkdir(parents=True)
    target.write_text(" \n", encoding="utf-8")

    result = load_snapshot_json(target, project_root=tmp_path)

    assert result.status == "INVALID_EMPTY"
    assert result.reason == "snapshot_file_empty"


def test_invalid_json_returns_safe_controlled_reason(tmp_path: Path) -> None:
    target = tmp_path / "data" / "reports" / "invalid.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"token":"do-not-echo",', encoding="utf-8")

    result = load_snapshot_json(target, project_root=tmp_path)

    assert result.status == "INVALID_JSON"
    assert result.reason == "snapshot_json_invalid:JSONDecodeError"
    assert "do-not-echo" not in result.reason


def test_incomplete_schema_returns_unknown_with_invalid_schema_metadata(tmp_path: Path) -> None:
    target = tmp_path / "data" / "reports" / "incomplete.json"
    write_json(target, {"schema_version": "expected_v1", "status": "OK"})

    snapshot = load_dashboard_snapshot(target, "expected_v1", project_root=tmp_path)

    assert snapshot["status"] == "UNKNOWN"
    assert snapshot["source_status"] == "INVALID_SCHEMA"
    assert snapshot["reason"].startswith("invalid_schema:")
    assert snapshot["live_release_allowed"] is False
    assert snapshot["canary_release_allowed"] is False


def test_stale_and_blocked_source_is_preserved_without_promotion(tmp_path: Path) -> None:
    target = tmp_path / "data" / "reports" / "stale.json"
    payload = valid_snapshot("expected_v1", ("health",))
    payload.update(
        {
            "status": "BLOCKED",
            "source_status": "BLOCKED",
            "freshness_status": "STALE",
            "last_updated_utc": "2026-01-01T00:00:00Z",
        }
    )
    write_json(target, payload)

    snapshot = load_dashboard_snapshot(target, "expected_v1", project_root=tmp_path)

    assert snapshot["status"] == "BLOCKED"
    assert snapshot["source_status"] == "BLOCKED"
    assert snapshot["freshness_status"] == "STALE"
    assert snapshot["last_updated_utc"] == "2026-01-01T00:00:00Z"


def test_path_outside_project_root_is_blocked(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.json"
    write_json(outside, {"status": "OK"})

    result = load_snapshot_json(outside, project_root=tmp_path)

    assert result.status == "BLOCKED"
    assert result.reason == "path_outside_project_root"


def test_cache_reader_has_short_ttl_and_fallback_without_streamlit(tmp_path: Path) -> None:
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"{}")
    captured: dict[str, Any] = {}

    class FakeStreamlit:
        def cache_data(self, **kwargs: Any) -> Callable[[Callable[..., bytes]], Callable[..., bytes]]:
            captured.update(kwargs)

            def decorate(function: Callable[..., bytes]) -> Callable[..., bytes]:
                captured["function"] = function.__name__
                return function

            return decorate

    cached_reader, cache_enabled = build_snapshot_byte_reader(FakeStreamlit())
    fallback_reader, fallback_enabled = build_snapshot_byte_reader(None)

    assert cache_enabled is True
    assert captured == {
        "ttl": SNAPSHOT_CACHE_TTL_SECONDS,
        "show_spinner": False,
        "function": "_read_snapshot_bytes",
    }
    assert cached_reader(str(target), target.stat().st_mtime_ns, target.stat().st_size) == b"{}"
    assert fallback_enabled is False
    assert fallback_reader(str(target), target.stat().st_mtime_ns, target.stat().st_size) == b"{}"


def test_file_signature_invalidates_cached_reader_contract(tmp_path: Path) -> None:
    target = tmp_path / "snapshot.json"
    target.write_text('{"value":1}', encoding="utf-8")
    first = load_snapshot_json(target, project_root=tmp_path)
    target.write_text('{"value":200}', encoding="utf-8")
    second = load_snapshot_json(target, project_root=tmp_path)

    assert first.data == {"value": 1}
    assert second.data == {"value": 200}
    assert (first.mtime_ns, first.size_bytes) != (second.mtime_ns, second.size_bytes)


def test_cache_is_confined_to_services_and_pages_remain_import_safe() -> None:
    assert "cache_data" in (SERVICES / "snapshot_json_loader.py").read_text(encoding="utf-8")
    for path in page_paths():
        source = path.read_text(encoding="utf-8")
        assert "cache_data" not in source
        assert "session_state" not in source
        module = load_page_module(path)
        assert callable(module.main)


def test_dashboard_loader_services_are_static_read_only() -> None:
    targets = [
        SERVICES / "snapshot_json_loader.py",
        SERVICES / "dashboard_snapshot_service.py",
        SERVICES / "page_snapshot_loader.py",
        SERVICES / "dashboard_file_loader.py",
    ]
    forbidden_calls = {
        "create_order",
        "market_buy",
        "fetch_balance",
        "NotificationDispatcher",
        "urlopen",
        "post",
    }
    forbidden_names = {"OrderManager", "NotificationDispatcher"}
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        source = path.read_text(encoding="utf-8").lower()
        assert calls.isdisjoint(forbidden_calls)
        assert names.isdisjoint(forbidden_names)
        assert "ccxt" not in source


def test_fail_closed_snapshot_preserves_all_safety_flags(tmp_path: Path) -> None:
    snapshot = load_dashboard_snapshot("missing.json", project_root=tmp_path)

    assert snapshot["paper_only"] is True
    assert snapshot["shadow_only"] is True
    for flag in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "canary_release_allowed",
        "live_release_allowed",
    ):
        assert snapshot[flag] is False
