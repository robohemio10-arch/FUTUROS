"""Safe badge HTML for the global dashboard safety posture."""


def render_status_badges(
    paper_shadow_only: bool = True,
    live_locked: bool = True,
    order_submission_disabled: bool = True,
    readiness_blocked: bool = True,
    riskmanager_authority: bool = True,
) -> str:
    """Return side-effect-free HTML for permanent safety badges."""

    badges = (
        (paper_shadow_only, "paper", "PAPER / SHADOW ONLY"),
        (live_locked, "live-locked", "LIVE LOCKED"),
        (order_submission_disabled, "order-disabled", "ORDER SUBMISSION DISABLED"),
        (readiness_blocked, "readiness-blocked", "READINESS BLOCKED"),
        (riskmanager_authority, "riskmanager", "RISKMANAGER AUTHORITY"),
    )
    return "".join(
        f'<span class="sfc-badge sfc-badge-{css_class}">{label}</span>'
        for enabled, css_class, label in badges
        if enabled
    )
