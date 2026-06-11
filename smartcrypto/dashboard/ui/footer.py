"""Auditable read-only footer."""

from html import escape
from typing import Any


_FOOTER_ITEMS = (
    "Dashboard Read-only",
    "Sem ccxt",
    "Sem create_order",
    "Sem OrderManager direto",
    "Sem live trading",
    "Sem private exchange read",
    "Sem CommandBus real",
    "Sem Telegram/NTFY real",
)


def render_footer_audit_bar(
    snapshot_name: str,
    extra_items: list[str] | None = None,
    *,
    ui: Any | None = None,
) -> str:
    """Render and return the permanent audit footer HTML."""

    items = (*_FOOTER_ITEMS, *(extra_items or []))
    item_html = "".join(f"<span>{escape(item)}</span>" for item in items)
    html = (
        '<footer class="sfc-footer"><div class="sfc-footer-snapshot">'
        f'Snapshot: {escape(snapshot_name)}</div><div class="sfc-footer-items">{item_html}</div></footer>'
    )
    if ui is not None:
        ui.markdown(html, unsafe_allow_html=True)
    elif _streamlit_context_available():
        _streamlit().markdown(html, unsafe_allow_html=True)
    return html


def _streamlit_context_available() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx(suppress_warning=True) is not None
    except ImportError:
        return False


def _streamlit() -> Any:
    import streamlit

    return streamlit
