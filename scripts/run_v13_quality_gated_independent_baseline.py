"""Preflight local inputs for the v13 quality-gated independent baseline."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="run_v13_quality_gated_independent_baseline",
    purpose="Inspect local data required by the v13 quality-gated independent baseline.",
    default_inputs=("data/training", "data/reports"),
    risks=("baseline outputs are research artifacts only",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
