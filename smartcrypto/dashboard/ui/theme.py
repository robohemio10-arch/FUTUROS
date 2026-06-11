"""Local-only CSS injection for the SMART FUTUROS dashboard."""

from pathlib import Path
from typing import Any


_CSS_PATH = Path(__file__).resolve().parents[1] / "assets" / "futuros_command_center.css"
_FALLBACK_CSS = """
html, body, [data-testid="stAppViewContainer"] { background: #020A12; color: #E6F1FF; }
.block-container { max-width: 100%; padding-top: .75rem; }
"""


def inject_smart_futuros_command_center_css(*, ui: Any | None = None) -> None:
    """Inject local institutional CSS once per Streamlit page."""

    target_ui = ui or _streamlit()
    try:
        css = _CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        css = _FALLBACK_CSS
    target_ui.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def inject_futuros_command_center_css(*, ui: Any | None = None) -> None:
    """Compatibility alias for the canonical CSS injector."""

    inject_smart_futuros_command_center_css(ui=ui)


def _streamlit() -> Any:
    import streamlit

    return streamlit
