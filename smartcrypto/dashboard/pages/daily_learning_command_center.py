"""Streamlit page for the read-only Daily Learning Command Center.

The page deliberately avoids buttons, forms, command dispatch, scheduler
registration, runtime writes, and any operation that could affect trading.
"""

from __future__ import annotations

from smartcrypto.dashboard.services.daily_learning_command_center import (
    build_daily_learning_command_center_view_model,
)


def render_daily_learning_command_center_page() -> None:
    """Render the read-only dashboard page when Streamlit is available."""

    try:
        import streamlit as st
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Streamlit is required to render this dashboard page.") from exc

    view_model = build_daily_learning_command_center_view_model()

    st.title(view_model["title"])
    st.caption(view_model["subtitle"])

    col_status, col_decision, col_mode = st.columns(3)
    col_status.metric("Status", str(view_model["status"]))
    col_decision.metric("Decision", str(view_model["decision"]))
    col_mode.metric("Input mode", str(view_model["input_mode"]))

    st.subheader("Research Sources")
    for card in view_model["cards"]:
        with st.container(border=True):
            st.markdown(f"**{card['title']}**")
            st.write(
                {
                    "status": card["status"],
                    "decision": card["decision"],
                    "input_mode": card["input_mode"],
                    "row_count": card["row_count"],
                    "safe_for_dashboard": card["safe_for_dashboard"],
                    "payload_provided": card["payload_provided"],
                }
            )
            st.caption(str(card["primary_note"]))

    st.subheader("Safety Gates")
    for gate in view_model["gates"]:
        marker = "PASS" if gate["passed"] else "BLOCKED"
        st.write(f"{marker} — {gate['gate_name']} [{gate['severity']}]")
        st.caption(str(gate["evidence"]))

    st.subheader("Safety Footer")
    st.write(view_model["safety_footer"])
    st.info(
        "Read-only surface. No scheduler execution, no orchestrator execution, "
        "no rule application, no feedback application, no training, no live/canary/orders."
    )


if __name__ == "__main__":  # pragma: no cover - Streamlit runtime path
    render_daily_learning_command_center_page()
