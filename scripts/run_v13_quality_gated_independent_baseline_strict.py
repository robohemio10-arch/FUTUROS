"""Preflight local inputs for the strict v13 quality-gated independent baseline."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="run_v13_quality_gated_independent_baseline_strict",
    purpose="Inspect strict baseline inputs without mutating datasets.",
    default_inputs=("data/training", "data/reports"),
    risks=("strict quality gates can reject incomplete local datasets",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
