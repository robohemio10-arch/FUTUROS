"""Run a safe preflight report for paper risk sizing with quality gates."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="run_paper_risk_sizing_quality_gated",
    purpose="Inspect local inputs for paper risk sizing quality-gated research.",
    default_inputs=("data/trades", "data/reports"),
    risks=("paper sizing is simulation-only and must not submit orders",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
