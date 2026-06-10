from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


DEFAULT_NTFY_SERVER_URL = "https://ntfy.sh"
DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TELEGRAM_TEXT_CHARS = 4096
MAX_NTFY_BODY_CHARS = 4096
MAX_TITLE_CHARS = 200
SAFE_RESULT_FLAGS: dict[str, Any] = {
    "paper_only": True,
    "shadow_only": True,
    "runtime_mode": "paper",
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
}
UrlOpen = Callable[[urllib.request.Request, float], Any]


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    body: str
    priority: str = "default"
    tags: tuple[str, ...] = field(default_factory=tuple)
    click_url: str | None = None
    correlation_id: str | None = None
    event_type: str | None = None
    severity: str | None = None

    def normalized(self) -> "NotificationMessage":
        title = normalize_space(self.title)[:MAX_TITLE_CHARS] or "FUTUROS notification"
        body = normalize_body(self.body, MAX_NTFY_BODY_CHARS) or title
        tags = tuple(tag.strip() for tag in self.tags if tag and tag.strip())
        return NotificationMessage(
            title=title,
            body=body,
            priority=normalize_priority(self.priority),
            tags=tags,
            click_url=normalize_optional(self.click_url),
            correlation_id=normalize_optional(self.correlation_id),
            event_type=normalize_optional(self.event_type),
            severity=normalize_optional(self.severity),
        )


@dataclass(frozen=True)
class NtfyConfig:
    enabled: bool = False
    topic: str = ""
    server_url: str = DEFAULT_NTFY_SERVER_URL
    token: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    api_base_url: str = DEFAULT_TELEGRAM_API_BASE_URL
    parse_mode: str = ""
    disable_notification: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class NotificationSettings:
    ntfy: NtfyConfig = field(default_factory=NtfyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    enabled: bool
    status: str
    reason: str
    http_status: int | None = None
    response_excerpt: str | None = None
    dry_run: bool = False
    paper_only: bool = True
    shadow_only: bool = True
    runtime_mode: str = "paper"
    live_trading_enabled: bool = False
    order_submission_enabled: bool = False
    real_order_submission_enabled: bool = False
    exchange_private_access: bool = False
    sends_orders: bool = False
    changes_risk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NotificationError(RuntimeError):
    pass


NTFY_HEADER_SAFE_TRANSLATION = str.maketrans(
    {
        "\u2014": "-",
        "\u2013": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def normalize_ntfy_header_value(value: Any, max_chars: int = MAX_TITLE_CHARS, *, fallback: str = "FUTUROS notification") -> str:
    text = normalize_space(value).translate(NTFY_HEADER_SAFE_TRANSLATION)
    text = text.encode("latin-1", errors="ignore").decode("latin-1")
    text = normalize_space(text)
    if not text:
        text = fallback
    return text[:max_chars]


def normalize_ntfy_tags(tags: Sequence[str]) -> str:
    safe_tags = [
        normalize_ntfy_header_value(tag, 64, fallback="")
        for tag in tags
        if tag and normalize_ntfy_header_value(tag, 64, fallback="")
    ]
    return ",".join(safe_tags)


class NtfyNotifier:
    def __init__(self, config: NtfyConfig, *, opener: UrlOpen | None = None) -> None:
        self.config = config
        self.opener: UrlOpen = opener if opener is not None else default_urlopen

    def send(self, message: NotificationMessage, *, dry_run: bool = False) -> DeliveryResult:
        if not self.config.enabled:
            return disabled("ntfy", dry_run=dry_run)
        validation_error = validate_ntfy_config(self.config)
        if validation_error:
            return blocked("ntfy", validation_error, dry_run=dry_run)

        msg = message.normalized()
        url = build_ntfy_url(self.config.server_url, self.config.topic)
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": normalize_ntfy_header_value(msg.title, MAX_TITLE_CHARS),
            "Priority": normalize_ntfy_header_value(msg.priority, 20, fallback="default"),
        }
        if msg.tags:
            tags_header = normalize_ntfy_tags(msg.tags)
            if tags_header:
                headers["Tags"] = tags_header
        if msg.click_url:
            headers["Click"] = normalize_ntfy_header_value(msg.click_url, 2048, fallback="")
        auth = ntfy_authorization_header(self.config)
        if auth:
            headers["Authorization"] = auth

        if dry_run:
            return sent("ntfy", reason="dry_run", dry_run=True)

        request = urllib.request.Request(
            url=url,
            data=msg.body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        return execute_request("ntfy", request, self.opener, self.config.timeout_seconds)


class TelegramNotifier:
    def __init__(self, config: TelegramConfig, *, opener: UrlOpen | None = None) -> None:
        self.config = config
        self.opener: UrlOpen = opener if opener is not None else default_urlopen

    def send(self, message: NotificationMessage, *, dry_run: bool = False) -> DeliveryResult:
        if not self.config.enabled:
            return disabled("telegram", dry_run=dry_run)
        validation_error = validate_telegram_config(self.config)
        if validation_error:
            return blocked("telegram", validation_error, dry_run=dry_run)
        msg = message.normalized()
        url = build_telegram_url(self.config.api_base_url, self.config.bot_token)
        payload: dict[str, Any] = {
            "chat_id": self.config.chat_id,
            "text": render_telegram_text(msg),
            "disable_notification": bool(self.config.disable_notification),
            "link_preview_options": {"is_disabled": True},
        }
        if self.config.parse_mode:
            payload["parse_mode"] = self.config.parse_mode
        if dry_run:
            return sent("telegram", reason="dry_run", dry_run=True)
        request = urllib.request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        return execute_request("telegram", request, self.opener, self.config.timeout_seconds)


class NotificationDispatcher:
    def __init__(
        self,
        settings: NotificationSettings,
        *,
        ntfy_opener: UrlOpen | None = None,
        telegram_opener: UrlOpen | None = None,
    ) -> None:
        self.ntfy = NtfyNotifier(settings.ntfy, opener=ntfy_opener)
        self.telegram = TelegramNotifier(settings.telegram, opener=telegram_opener)

    def send(self, message: NotificationMessage, *, dry_run: bool = False) -> list[DeliveryResult]:
        return [self.ntfy.send(message, dry_run=dry_run), self.telegram.send(message, dry_run=dry_run)]


def settings_from_env(env: Mapping[str, str] | None = None) -> NotificationSettings:
    values: Mapping[str, str] = env if env is not None else os.environ
    timeout = env_float(values, "SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    return NotificationSettings(
        ntfy=NtfyConfig(
            enabled=env_bool(values, "SMARTCRYPTO_NTFY_ENABLED", False),
            topic=values.get("SMARTCRYPTO_NTFY_TOPIC", "").strip(),
            server_url=(
                values.get("SMARTCRYPTO_NTFY_SERVER_URL", DEFAULT_NTFY_SERVER_URL).strip()
                or DEFAULT_NTFY_SERVER_URL
            ),
            token=values.get("SMARTCRYPTO_NTFY_TOKEN", "").strip(),
            username=values.get("SMARTCRYPTO_NTFY_USERNAME", "").strip(),
            password=values.get("SMARTCRYPTO_NTFY_PASSWORD", "").strip(),
            timeout_seconds=timeout,
        ),
        telegram=TelegramConfig(
            enabled=env_bool(values, "SMARTCRYPTO_TELEGRAM_ENABLED", False),
            bot_token=values.get("SMARTCRYPTO_TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=values.get("SMARTCRYPTO_TELEGRAM_CHAT_ID", "").strip(),
            api_base_url=values.get("SMARTCRYPTO_TELEGRAM_API_BASE_URL", DEFAULT_TELEGRAM_API_BASE_URL).strip()
            or DEFAULT_TELEGRAM_API_BASE_URL,
            parse_mode=values.get("SMARTCRYPTO_TELEGRAM_PARSE_MODE", "").strip(),
            disable_notification=env_bool(values, "SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION", False),
            timeout_seconds=timeout,
        ),
    )


def message_from_alert_report(report: dict[str, Any], *, include_warning_alerts: bool = True) -> NotificationMessage:
    status = str(report.get("status") or "unknown")
    reason = str(report.get("reason") or "no_reason")
    critical_alerts = list_of_dicts(report.get("critical_alerts"))
    warning_alerts = list_of_dicts(report.get("warning_alerts"))
    selected_alerts: list[dict[str, Any]] = list(critical_alerts)
    if include_warning_alerts:
        selected_alerts.extend(warning_alerts)
    top_alerts = selected_alerts[:5]
    summary = dict_or_empty(report.get("summary"))
    lines = [
        f"status={status}",
        f"reason={reason}",
        f"total_events={report.get('total_events', summary.get('total_events', 0))}",
        f"critical_alerts={len(critical_alerts)}",
        f"warning_alerts={len(warning_alerts)}",
    ]
    latest = summary.get("latest_event_at_utc")
    if latest:
        lines.append(f"latest_event_at_utc={latest}")
    for index, alert_payload in enumerate(top_alerts, start=1):
        event_type = alert_payload.get("event_type") or "unknown_event"
        alert_reason = alert_payload.get("reason") or "unknown_reason"
        symbol = alert_payload.get("symbol") or "-"
        correlation_id = alert_payload.get("correlation_id") or "-"
        lines.append(f"alert_{index}={event_type}|{alert_reason}|symbol={symbol}|corr={correlation_id}")
    severity = "critical" if status == "blocked" or critical_alerts else "warning" if warning_alerts else "info"
    priority = "urgent" if severity == "critical" else "high" if severity == "warning" else "default"
    tags: tuple[str, ...]
    if severity == "critical":
        tags = ("warning", "rotating_light")
    elif severity == "warning":
        tags = ("warning",)
    else:
        tags = ("white_check_mark",)
    return NotificationMessage(
        title=f"FUTUROS alerting: {status}",
        body="\n".join(lines),
        priority=priority,
        tags=tags,
        correlation_id=first_correlation_id(top_alerts),
        event_type=first_event_type(top_alerts),
        severity=severity,
    ).normalized()


def should_dispatch_alert_report(report: dict[str, Any], *, include_ok: bool = False) -> bool:
    if include_ok:
        return True
    return str(report.get("status") or "").lower() in {"blocked", "warning", "missing_data"}


def dispatch_alert_report(
    report: dict[str, Any],
    *,
    settings: NotificationSettings | None = None,
    dry_run: bool = False,
    include_ok: bool = False,
    ntfy_opener: UrlOpen | None = None,
    telegram_opener: UrlOpen | None = None,
) -> dict[str, Any]:
    if not should_dispatch_alert_report(report, include_ok=include_ok):
        return {
            "status": "skipped",
            "reason": "alert_report_status_ok",
            "dispatch_attempted": False,
            "results": [],
            **SAFE_RESULT_FLAGS,
        }
    dispatcher = NotificationDispatcher(
        settings or settings_from_env(),
        ntfy_opener=ntfy_opener,
        telegram_opener=telegram_opener,
    )
    message = message_from_alert_report(report)
    results = dispatcher.send(message, dry_run=dry_run)
    result_payloads = [result.to_dict() for result in results]
    failed_results = [row for row in result_payloads if row["status"] in {"failed", "blocked"}]
    sent_count = sum(1 for row in result_payloads if row["status"] == "sent")
    enabled_count = sum(1 for row in result_payloads if row["enabled"] is True)
    if failed_results:
        status = "blocked"
        reason = ";".join(sorted({str(row["reason"]) for row in failed_results}))
    elif sent_count:
        status = "ok"
        reason = "sent"
    elif enabled_count == 0:
        status = "disabled"
        reason = "all_channels_disabled"
    else:
        status = "warning"
        reason = "no_channel_sent"
    return {
        "status": status,
        "reason": reason,
        "dispatch_attempted": True,
        "dry_run": bool(dry_run),
        "message": asdict(message),
        "results": result_payloads,
        **SAFE_RESULT_FLAGS,
    }


def load_alert_report(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"status": "missing_data", "reason": "missing_alert_report", "path": str(target)}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "blocked", "reason": f"invalid_alert_report_json:{exc}", "path": str(target)}
    if not isinstance(payload, dict):
        return {"status": "blocked", "reason": "invalid_alert_report_payload", "path": str(target)}
    return payload


def write_dispatch_report(path: str | Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def execute_request(
    channel: str,
    request: urllib.request.Request,
    opener: UrlOpen,
    timeout_seconds: float,
) -> DeliveryResult:
    try:
        with opener(request, float(timeout_seconds)) as response:
            status_code = int(getattr(response, "status", getattr(response, "code", 0)) or 0)
            body = response.read(512).decode("utf-8", errors="replace") if hasattr(response, "read") else ""
    except urllib.error.HTTPError as exc:
        body = exc.read(512).decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        return failed(channel, f"http_error:{exc.code}", http_status=int(exc.code), response_excerpt=body)
    except urllib.error.URLError as exc:
        return failed(channel, f"url_error:{exc.reason}")
    except TimeoutError:
        return failed(channel, "timeout")
    except OSError as exc:
        return failed(channel, f"os_error:{exc}")
    except Exception as exc:  # pragma: no cover - defensive boundary around external IO
        return failed(channel, f"unexpected_error:{type(exc).__name__}:{exc}")
    if 200 <= status_code < 300:
        return sent(channel, reason="http_ok", http_status=status_code, response_excerpt=body)
    return failed(channel, f"unexpected_http_status:{status_code}", http_status=status_code, response_excerpt=body)


def default_urlopen(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def validate_ntfy_config(config: NtfyConfig) -> str | None:
    if not config.topic.strip():
        return "missing_ntfy_topic"
    if not config.server_url.startswith(("https://", "http://")):
        return "invalid_ntfy_server_url"
    if bool(config.username) != bool(config.password):
        return "invalid_ntfy_basic_auth_pair"
    if config.token and (config.username or config.password):
        return "ambiguous_ntfy_auth"
    if config.timeout_seconds <= 0:
        return "invalid_timeout_seconds"
    return None


def validate_telegram_config(config: TelegramConfig) -> str | None:
    if not config.bot_token.strip():
        return "missing_telegram_bot_token"
    if not config.chat_id.strip():
        return "missing_telegram_chat_id"
    if not config.api_base_url.startswith(("https://", "http://")):
        return "invalid_telegram_api_base_url"
    if config.parse_mode and config.parse_mode not in {"MarkdownV2", "HTML", "Markdown"}:
        return "invalid_telegram_parse_mode"
    if config.timeout_seconds <= 0:
        return "invalid_timeout_seconds"
    return None


def build_ntfy_url(server_url: str, topic: str) -> str:
    return f"{server_url.rstrip('/')}/{urllib.parse.quote(topic.strip(), safe='')}"


def build_telegram_url(api_base_url: str, bot_token: str) -> str:
    token = urllib.parse.quote(bot_token.strip(), safe=":")
    return f"{api_base_url.rstrip('/')}/bot{token}/sendMessage"


def ntfy_authorization_header(config: NtfyConfig) -> str | None:
    if config.token:
        return f"Bearer {config.token}"
    if config.username and config.password:
        raw = f"{config.username}:{config.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
    return None


def render_telegram_text(message: NotificationMessage) -> str:
    body = f"{message.title}\n\n{message.body}"
    return normalize_body(body, MAX_TELEGRAM_TEXT_CHARS)


def normalize_priority(value: str) -> str:
    normalized = str(value or "default").strip().lower()
    allowed = {"min", "low", "default", "high", "urgent", "1", "2", "3", "4", "5"}
    return normalized if normalized in allowed else "default"


def normalize_space(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_body(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def normalize_optional(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def env_bool(values: Mapping[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(values: Mapping[str, str], key: str, default: float) -> float:
    raw = values.get(key)
    if raw is None or raw == "":
        return float(default)
    try:
        parsed = float(str(raw).strip())
    except ValueError:
        return float(default)
    return parsed if parsed > 0 else float(default)


def dict_or_empty(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value


def list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def first_correlation_id(alerts: Sequence[Mapping[str, Any]]) -> str | None:
    for alert_payload in alerts:
        if alert_payload.get("correlation_id"):
            return str(alert_payload["correlation_id"])
    return None


def first_event_type(alerts: Sequence[Mapping[str, Any]]) -> str | None:
    for alert_payload in alerts:
        if alert_payload.get("event_type"):
            return str(alert_payload["event_type"])
    return None


def disabled(channel: str, *, dry_run: bool) -> DeliveryResult:
    return DeliveryResult(channel=channel, enabled=False, status="disabled", reason="channel_disabled", dry_run=dry_run)


def blocked(channel: str, reason: str, *, dry_run: bool) -> DeliveryResult:
    return DeliveryResult(channel=channel, enabled=True, status="blocked", reason=reason, dry_run=dry_run)


def failed(
    channel: str,
    reason: str,
    *,
    http_status: int | None = None,
    response_excerpt: str | None = None,
) -> DeliveryResult:
    return DeliveryResult(
        channel=channel,
        enabled=True,
        status="failed",
        reason=reason,
        http_status=http_status,
        response_excerpt=normalize_body(response_excerpt, 512) if response_excerpt else None,
    )


def sent(
    channel: str,
    *,
    reason: str,
    dry_run: bool = False,
    http_status: int | None = None,
    response_excerpt: str | None = None,
) -> DeliveryResult:
    return DeliveryResult(
        channel=channel,
        enabled=True,
        status="sent",
        reason=reason,
        http_status=http_status,
        response_excerpt=normalize_body(response_excerpt, 512) if response_excerpt else None,
        dry_run=dry_run,
    )
