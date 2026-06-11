from __future__ import annotations

import re

from smartcrypto.dashboard.ui import tokens


HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_required_color_and_status_tokens_are_valid_hex() -> None:
    required = (
        "COLOR_BG_MAIN", "COLOR_BG_PANEL", "COLOR_BG_CARD", "COLOR_TEXT_PRIMARY",
        "COLOR_CYAN", "COLOR_GREEN", "COLOR_RED", "COLOR_YELLOW", "COLOR_PURPLE",
        "STATUS_OK", "STATUS_INFO", "STATUS_WARNING", "STATUS_ERROR", "STATUS_BLOCKED",
        "STATUS_HARD_BLOCKED", "STATUS_READONLY", "STATUS_PAPER", "STATUS_SHADOW",
        "STATUS_DISABLED", "STATUS_UNKNOWN",
    )
    for name in required:
        value = getattr(tokens, name)
        assert HEX_COLOR.fullmatch(value), f"{name}={value}"


def test_gradients_shadows_typography_and_spacing_are_nonempty() -> None:
    prefixes = ("GRADIENT_", "SHADOW_", "FONT_", "SPACE_", "RADIUS_")
    selected = {
        name: value
        for name, value in vars(tokens).items()
        if name.startswith(prefixes) or name in {"PANEL_PADDING", "CARD_PADDING", "TABLE_CELL_PADDING"}
    }
    assert selected
    assert all(value not in {None, ""} for value in selected.values())
    assert "linear-gradient" in tokens.GRADIENT_CARD_BLUE
    assert "Inter" in tokens.FONT_FAMILY


def test_critical_status_colors_are_not_accidentally_collapsed() -> None:
    assert tokens.STATUS_OK != tokens.STATUS_BLOCKED
    assert tokens.STATUS_WARNING != tokens.STATUS_READONLY
    assert tokens.STATUS_SHADOW != tokens.STATUS_PAPER
