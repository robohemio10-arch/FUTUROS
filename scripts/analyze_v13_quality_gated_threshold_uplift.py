"""Analyze local inputs for v13 quality-gated threshold uplift research."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="analyze_v13_quality_gated_threshold_uplift",
    purpose="Inspect local reports used for threshold uplift analysis.",
    default_inputs=("data/reports",),
    risks=("threshold uplift is research-only and cannot increase risk automatically",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
