"""Diagnose local trade datetime parsing assumptions."""

from __future__ import annotations

from audit_diagnostic_common import ScriptSpec, main_for


SPEC = ScriptSpec(
    name="diagnose_trade_datetime_parse",
    purpose="Inspect local trade files used for datetime parsing diagnostics.",
    default_inputs=("data/trades",),
    risks=("locale and timezone assumptions must be reviewed before training",),
)


if __name__ == "__main__":
    raise SystemExit(main_for(SPEC))
