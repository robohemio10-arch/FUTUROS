"""Prepare a safe preflight report for quality-gated training dataset builds."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="build_training_dataset_quality_gated_binance_1m",
    purpose="Inspect local data required for a quality-gated Binance 1m training dataset.",
    default_inputs=("data/training", "data/binance", "data/trades"),
    risks=("training artifacts must remain runtime outputs",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
