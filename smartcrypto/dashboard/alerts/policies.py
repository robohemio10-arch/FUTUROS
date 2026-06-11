from __future__ import annotations

from typing import Any


def build_notification_stub_audit() -> dict[str, Any]:
    return {
        "project_name": "SMART FUTUROS",
        "dashboard_name": "SMART FUTUROS Command Center",
        "notification_stub_only": True,
        "dashboard_reads_only": True,
        "sent": False,
        "sends_notifications": False,
        "uses_telegram_token": False,
        "uses_ntfy_token": False,
        "uses_requests_post": False,
        "uses_httpx_post": False,
        "uses_aiohttp": False,
        "writes_runtime": False,
        "uses_private_exchange": False,
        "sends_orders": False,
    }
