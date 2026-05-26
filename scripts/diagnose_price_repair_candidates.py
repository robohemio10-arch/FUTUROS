"""Identify local inputs for price repair candidate diagnostics."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="diagnose_price_repair_candidates",
    purpose="Inspect local data needed to review possible price repair candidates.",
    default_inputs=("data/trades", "data/binance"),
    risks=("script does not repair data; analyst approval is required",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
