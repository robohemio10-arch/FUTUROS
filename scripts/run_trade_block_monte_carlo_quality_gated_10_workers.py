"""Preflight local inputs for block Monte Carlo research with quality gates."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="run_trade_block_monte_carlo_quality_gated_10_workers",
    purpose="Inspect inputs for block Monte Carlo research using local quality-gated data.",
    default_inputs=("data/trades", "data/reports"),
    risks=("multi-worker research can be CPU intensive",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
