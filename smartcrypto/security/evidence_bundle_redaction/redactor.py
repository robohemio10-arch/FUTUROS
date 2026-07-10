"""Deterministic text secret detection and redaction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Pattern

from .contracts import RedactionResult, SecretFinding

REDACTION_PREFIX_LENGTH = 12
SENSITIVE_NAME = (
    r"(?:token|secret|password|pass|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"webhook|auth(?:orization)?|credential(?![_-](?:id|category)\b))"
)


@dataclass(frozen=True)
class PatternRule:
    name: str
    category: str
    pattern: Pattern[str]
    secret_group: str
    severity: str = "high"


def _compile(pattern: str, flags: int = 0) -> Pattern[str]:
    return re.compile(pattern, flags)


RULES: tuple[PatternRule, ...] = (
    PatternRule(
        "pem_private_key",
        "private_key",
        _compile(
            r"(?P<secret>-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----)",
            re.DOTALL,
        ),
        "secret",
        "critical",
    ),
    PatternRule(
        "github_pat",
        "github_token",
        _compile(r"(?P<secret>\bgh[pousr]_[A-Za-z0-9_]{30,}\b)"),
        "secret",
        "critical",
    ),
    PatternRule(
        "jwt",
        "jwt",
        _compile(r"(?P<secret>\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)"),
        "secret",
    ),
    PatternRule(
        "telegram_bot_token",
        "telegram_token",
        _compile(r"(?P<secret>\b\d{6,12}:[A-Za-z0-9_-]{20,}\b)"),
        "secret",
        "critical",
    ),
    PatternRule(
        "authorization_header",
        "authorization",
        _compile(
            r"(?i)(?:authorization\s*[:=]\s*[\"']?(?:bearer\s+)?)(?P<secret>[^\s\"',}\]]{8,})"
        ),
        "secret",
        "critical",
    ),
    PatternRule(
        "bearer_token",
        "bearer_token",
        _compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/-]{12,}=*)"),
        "secret",
    ),
    PatternRule(
        "authenticated_url",
        "authenticated_url",
        _compile(r"(?i)https?://(?P<secret>[^/\s:@]+:[^@\s/]+)@"),
        "secret",
        "critical",
    ),
    PatternRule(
        "sensitive_query_parameter",
        "url_query_secret",
        _compile(rf"(?i)[?&](?:{SENSITIVE_NAME})=(?P<secret>[^&#\s\"']{{4,}})"),
        "secret",
    ),
    PatternRule(
        "quoted_named_secret_assignment",
        "named_secret",
        _compile(
            rf"(?i)[\"']?[A-Za-z0-9_.-]*{SENSITIVE_NAME}[A-Za-z0-9_.-]*[\"']?"
            rf"\s*[:=]\s*(?P<quote>[\"'])(?P<secret>[^\"'\r\n]{{4,}})(?P=quote)"
        ),
        "secret",
        "critical",
    ),
    PatternRule(
        "named_secret_assignment",
        "named_secret",
        _compile(
            rf"(?i)[\"']?[A-Za-z0-9_.-]*{SENSITIVE_NAME}[A-Za-z0-9_.-]*[\"']?"
            rf"\s*[:=]\s*[\"']?(?P<secret>[^\s\"',}}\]]{{4,}})"
        ),
        "secret",
        "critical",
    ),
    PatternRule(
        "generic_api_key",
        "api_key",
        _compile(r"(?P<secret>\b(?:sk|rk|pk)[_-](?:live|test|prod)?[_-]?[A-Za-z0-9]{20,}\b)"),
        "secret",
    ),
)


@dataclass(frozen=True)
class _SecretMatch:
    start: int
    end: int
    rule: PatternRule
    secret: str


def redact_text(text: str, *, relative_path: str) -> RedactionResult:
    """Return redacted text and metadata that never includes secret material."""

    matches = _collect_non_overlapping_matches(text)
    findings: list[SecretFinding] = []
    redacted = text
    for item in reversed(matches):
        fingerprint = hashlib.sha256(item.secret.encode("utf-8")).hexdigest()
        marker = f"<REDACTED:{item.rule.category}:{fingerprint[:REDACTION_PREFIX_LENGTH]}>"
        line, column = _line_column(text, item.start)
        finding_id = hashlib.sha256(
            f"{relative_path}|{line}|{column}|{item.rule.name}|{fingerprint}".encode("utf-8")
        ).hexdigest()
        findings.append(
            SecretFinding(
                finding_id=finding_id,
                category=item.rule.category,
                severity=item.rule.severity,
                relative_path=relative_path.replace("\\", "/"),
                line_number=line,
                column_number=column,
                matched_pattern_name=item.rule.name,
                redacted_preview=marker,
                secret_fingerprint_sha256=fingerprint,
                blocking=True,
                remediation="remove_or_rotate_secret_and_regenerate_sanitized_evidence",
            )
        )
        redacted = redacted[: item.start] + marker + redacted[item.end :]
    findings.reverse()
    return RedactionResult(redacted_text=redacted, findings=tuple(findings))


def contains_secret(text: str) -> bool:
    return bool(_collect_non_overlapping_matches(text))


def _collect_non_overlapping_matches(text: str) -> list[_SecretMatch]:
    candidates: list[_SecretMatch] = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            secret = match.group(rule.secret_group)
            start, end = match.span(rule.secret_group)
            if secret and not _is_placeholder(secret):
                candidates.append(_SecretMatch(start=start, end=end, rule=rule, secret=secret))
    candidates.sort(key=lambda item: (item.start, -(item.end - item.start), item.rule.name))
    accepted: list[_SecretMatch] = []
    last_end = -1
    for candidate in candidates:
        if candidate.start < last_end:
            continue
        accepted.append(candidate)
        last_end = candidate.end
    return accepted


def _is_placeholder(secret: str) -> bool:
    normalized = secret.strip().casefold()
    return (
        normalized.startswith("${")
        or normalized.startswith("<redacted:")
        or normalized in {"bearer", "changeme", "example", "placeholder", "redacted"}
    )


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line_number = text.count("\n", 0, offset) + 1
    previous_newline = text.rfind("\n", 0, offset)
    column_number = offset - previous_newline
    return line_number, column_number
