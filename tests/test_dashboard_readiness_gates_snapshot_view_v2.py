from __future__ import annotations

import ast

from smartcrypto.dashboard.components.readiness_gates import (
    build_readiness_unknown_state,
    extract_readiness_gates,
)


def test_missing_readiness_is_optional_unknown_and_release_blocked() -> None:
    state = build_readiness_unknown_state()
    assert state["status"] == "MISSING_OPTIONAL"
    assert state["canary_release_allowed"] is False
    assert state["live_release_allowed"] is False
    assert state["manual_go_no_go_required"] is True


def test_readiness_snapshot_never_auto_releases_canary_or_live() -> None:
    state = extract_readiness_gates(
        {"sections": {"readiness": {
            "seven_day_diagnostic_status": "OK",
            "thirty_day_readiness_status": "OK",
            "canary_release_allowed": True,
            "live_release_allowed": True,
            "blocking_reasons": ["manual_go_no_go_required"],
        }}}
    )
    assert state["canary_release_allowed"] is False
    assert state["live_release_allowed"] is False
    assert state["manual_go_no_go_required"] is True
    assert state["blocking_reasons"] == ["manual_go_no_go_required"]


def test_readiness_component_has_no_runner_or_writer_calls() -> None:
    from smartcrypto.dashboard.components import readiness_gates

    text = open(readiness_gates.__file__, encoding="utf-8").read()
    tree = ast.parse(text)
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert calls.isdisjoint({"run_soak", "run_monte_carlo", "generate_evidence_pack", "write_text"})
