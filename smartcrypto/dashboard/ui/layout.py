"""Shared Streamlit layout primitives."""

from html import escape
from typing import Any

from .badges import render_status_badges


def render_global_topbar(
    last_updated: str | None = None,
    utc_now: str | None = None,
    title: str = "SMART FUTUROS",
    subtitle: str = "Command Center",
    *,
    ui: Any | None = None,
) -> None:
    target_ui = ui or _streamlit()
    timestamps = "".join(
        (
            f'<span><b>Snapshot</b> {escape(last_updated or "UNKNOWN")}</span>',
            f'<span><b>UTC</b> {escape(utc_now or last_updated or "UNKNOWN")}</span>',
        )
    )
    html = (
        '<header class="sfc-topbar"><div class="sfc-brand">'
        f'<div class="sfc-brand-title">{escape(title)} <span>{escape(subtitle)}</span></div>'
        '<div class="sfc-brand-subtitle">Institutional Dashboard</div></div>'
        f'<div class="sfc-topbar-status">{render_status_badges()}</div>'
        f'<div class="sfc-topbar-time">{timestamps}</div></header>'
    )
    target_ui.markdown(html, unsafe_allow_html=True)


def render_page_title(
    number: str,
    title: str,
    subtitle: str,
    *,
    ui: Any | None = None,
) -> None:
    target_ui = ui or _streamlit()
    target_ui.title(f"{number}. {title}" if number else title)
    target_ui.markdown(
        f'<div class="sfc-page-subtitle">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )


def render_two_column_layout(
    left_ratio: int = 1,
    right_ratio: int = 3,
    *,
    ui: Any | None = None,
) -> Any:
    return (ui or _streamlit()).columns((left_ratio, right_ratio))


def render_readonly_banner(*, ui: Any | None = None) -> None:
    (ui or _streamlit()).markdown(
        '<div class="sfc-readonly-banner">SNAPSHOT-FIRST · READ-ONLY · PAPER / SHADOW ONLY</div>',
        unsafe_allow_html=True,
    )


def _streamlit() -> Any:
    import streamlit

    return streamlit
