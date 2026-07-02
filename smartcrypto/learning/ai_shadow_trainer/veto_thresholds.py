"""Threshold summaries for research-only AI Shadow candidate veto evidence."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .veto_metrics import rounded_sum


def threshold_by_symbol_side_regime(decision_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not decision_rows:
        return []
    frame = pd.DataFrame(decision_rows)
    rows: list[dict[str, Any]] = []
    for (symbol, side, regime), group in frame.groupby(["symbol", "side", "regime"], dropna=False, sort=True):
        accepted = group[group["ai_shadow_candidate_decision"] == "AI_ACCEPT"]
        threshold = 0.5 if accepted.empty else float(accepted["probability_quality"].min())
        rows.append(
            {
                "symbol": str(symbol),
                "side": str(side),
                "regime": str(regime),
                "regime_source": "default_global",
                "threshold_quality": round(threshold, 10),
                "threshold_scope": "symbol_side_regime",
                "rows": int(len(group)),
                "accepted_count": int(len(accepted)),
                "rejected_count": int((group["ai_shadow_candidate_decision"] == "AI_REJECT").sum()),
                "accepted_expected_value": rounded_sum(accepted.get("target_expected_value_component", pd.Series(dtype=float))),
                "research_only": True,
                "veto_runtime_active": False,
            }
        )
    return rows
