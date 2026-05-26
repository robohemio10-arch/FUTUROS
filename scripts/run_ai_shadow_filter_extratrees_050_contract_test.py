"""Run a safe preflight report for the AI shadow filter contract test."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="run_ai_shadow_filter_extratrees_050_contract_test",
    purpose="Inspect local artifacts for the ExtraTrees 0.50 AI shadow contract test.",
    default_inputs=("data/models", "data/reports"),
    risks=("AI shadow filters must not increase risk or submit orders",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
