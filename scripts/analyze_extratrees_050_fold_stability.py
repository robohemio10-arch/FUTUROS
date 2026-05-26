"""Analyze local inputs for ExtraTrees 0.50 fold stability research."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="analyze_extratrees_050_fold_stability",
    purpose="Inspect local model reports used for ExtraTrees 0.50 fold stability analysis.",
    default_inputs=("data/reports", "data/models"),
    risks=("analysis is read-only and depends on local research artifacts",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
