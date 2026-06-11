"""Institutional empty, unknown and error states."""

from html import escape


def render_empty_state(message: str = "Sem dados disponíveis") -> str:
    return _state_html("empty", "EMPTY", message)


def render_unknown_state(message: str = "Fonte ausente ou ainda não gerada") -> str:
    return _state_html("unknown", "UNKNOWN", message)


def render_error_state(
    message: str = "Erro ao carregar snapshot",
    details: str | None = None,
) -> str:
    details_html = f'<div class="sfc-state-details">{escape(details)}</div>' if details else ""
    return _state_html("error", "ERROR", message, details_html)


def _state_html(kind: str, label: str, message: str, details_html: str = "") -> str:
    return (
        f'<div class="sfc-state sfc-state-{kind}"><strong>{label}</strong>'
        f'<span>{escape(message)}</span>{details_html}</div>'
    )
