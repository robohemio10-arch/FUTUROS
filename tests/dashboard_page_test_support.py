from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from smartcrypto.ops.dashboard_snapshots.contracts import DashboardAuditContract


ROOT = Path(__file__).resolve().parents[1]
PAGES_DIR = ROOT / "smartcrypto" / "dashboard" / "pages"


class FakeUi:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def __enter__(self) -> FakeUi:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def _record(self, name: str, value: Any = None, **_kwargs: Any) -> None:
        self.events.append((name, value))

    def columns(self, count: int) -> list[FakeUi]:
        return [self for _ in range(count)]

    def expander(self, label: str, **_kwargs: Any) -> FakeUi:
        self._record("expander", label)
        return self

    def metric(self, label: str, value: Any, **_kwargs: Any) -> None:
        self._record("metric", (label, value))

    def page_link(self, target: str, **kwargs: Any) -> None:
        self._record("page_link", (target, kwargs.get("label")))

    def __getattr__(self, name: str) -> Any:
        def recorder(value: Any = None, **kwargs: Any) -> None:
            self._record(name, value, **kwargs)

        return recorder


def load_page_module(path: Path) -> ModuleType:
    module_name = f"dashboard_page_test_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable_to_import:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page_paths() -> list[Path]:
    return sorted(path for path in PAGES_DIR.glob("[0-9][0-9]_*.py"))


def valid_snapshot(schema_version: str, section_names: tuple[str, ...]) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "runtime_mode": "paper",
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": "2026-06-11T12:00:00Z",
        "status_summary": {"status": "OK"},
        "sections": {
            name: {"status": "OK", "reason": "fixture"} for name in section_names
        },
        "audit": DashboardAuditContract(snapshot_source="test_fixture").to_dict(),
    }
