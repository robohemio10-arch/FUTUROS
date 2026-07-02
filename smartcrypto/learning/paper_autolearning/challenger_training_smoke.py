"""Advisory smoke checks for future challenger training."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def run_challenger_training_smoke(
    microbatch_rows: Sequence[Mapping[str, Any]],
    *,
    enabled: bool = False,
) -> dict[str, Any]:
    if not enabled:
        return {
            "qlib_challenger_smoke_ran": False,
            "ai_shadow_challenger_smoke_ran": False,
            "qlib_challenger_trained": False,
            "ai_shadow_challenger_trained": False,
            "training_smoke_status": "skipped",
            "training_smoke_reason": "train_smoke_not_requested",
            "training_smoke_metrics": {},
        }
    rows = [dict(row) for row in microbatch_rows]
    labels = [row.get("label_sign") for row in rows if row.get("label_sign") in {-1, 0, 1}]
    feature_columns = sorted({column for row in rows for column in row if str(column).startswith("feature_")})
    class_balance = {str(label): labels.count(label) for label in sorted(set(labels), key=str)}
    return {
        "qlib_challenger_smoke_ran": True,
        "ai_shadow_challenger_smoke_ran": True,
        "qlib_challenger_trained": False,
        "ai_shadow_challenger_trained": False,
        "training_smoke_status": "ok" if rows and feature_columns and len(set(labels)) >= 2 else "warning",
        "training_smoke_reason": "advisory_smoke_only_no_model_artifact_created",
        "training_smoke_metrics": {
            "rows": len(rows),
            "feature_count": len(feature_columns),
            "label_count": len(labels),
            "class_balance": class_balance,
            "can_train_future_challenger": bool(rows and feature_columns and len(set(labels)) >= 2),
        },
    }
