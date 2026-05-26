"""Preflight local inputs for AI shadow filter ExtraTrees 0.50 training."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="train_ai_shadow_filter_extratrees_050",
    purpose="Inspect local training inputs for the ExtraTrees 0.50 AI shadow filter.",
    default_inputs=("data/training", "data/reports"),
    risks=("training output must remain runtime data and cannot alter live risk",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
