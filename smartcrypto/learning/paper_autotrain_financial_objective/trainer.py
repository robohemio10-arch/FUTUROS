"""Financially weighted challenger training confined to research quarantine."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd

from smartcrypto.learning.paper_autotrain_daily_quarantine_activation.activation import (
    QuarantinePaths,
    challenger_blocked,
    challenger_unavailable,
    feature_columns,
    file_sha256,
    sklearn_available,
    write_json,
)

from .contracts import FINANCIAL_OBJECTIVES


class FinancialObjectiveTrainerBackend:
    """Quarantine trainer that applies bounded financial sample weights."""

    def train_challenger(
        self,
        *,
        root: Path,
        run_id: str,
        backend_id: str,
        microbatch: pd.DataFrame,
        paths: QuarantinePaths,
        write_artifact: bool,
    ) -> dict[str, Any]:
        if backend_id == "qlib":
            backend_available = importlib.util.find_spec("qlib") is not None
            unavailable_reason = "qlib_backend_unavailable"
        elif backend_id == "ai_shadow":
            backend_available = sklearn_available()
            unavailable_reason = "ai_shadow_backend_unavailable"
        else:
            return challenger_blocked(backend_id, "unknown_trainer_backend")
        if not backend_available:
            return challenger_unavailable(backend_id, unavailable_reason)

        features = feature_columns(microbatch)
        if not features:
            return challenger_blocked(backend_id, "missing_feature_columns")
        if microbatch["target_profitable"].nunique(dropna=True) < 2:
            return challenger_blocked(backend_id, "single_class_target")
        try:
            from sklearn.impute import SimpleImputer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            return challenger_unavailable(
                backend_id, f"{backend_id}_sklearn_backend_unavailable"
            )

        x_train = microbatch[features]
        y_train = microbatch["target_profitable"].astype(int)
        sample_weight = pd.to_numeric(
            microbatch.get(
                "financial_sample_weight",
                pd.Series(1.0, index=microbatch.index, dtype=float),
            ),
            errors="coerce",
        ).fillna(1.0).clip(lower=0.25, upper=5.0)
        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("classifier", LogisticRegression(max_iter=500, random_state=17)),
            ]
        )
        model.fit(x_train, y_train, classifier__sample_weight=sample_weight)
        classifier = model.named_steps["classifier"]
        probabilities = model.predict_proba(x_train)[:, 1]
        candidate = {
            "candidate_id": f"{backend_id}_{run_id}",
            "backend_id": backend_id,
            "status": "trained_quarantine_only",
            "row_count": int(len(microbatch)),
            "feature_count": int(len(features)),
            "class_balance": {
                str(key): int(value)
                for key, value in y_train.value_counts().sort_index().to_dict().items()
            },
            "mean_probability": round(float(probabilities.mean()), 10),
            "coefficients": classifier.coef_.tolist(),
            "intercept": classifier.intercept_.tolist(),
            "classes": [int(value) for value in classifier.classes_],
            "financial_objective_applied": "financial_sample_weight" in microbatch.columns,
            "financial_sample_weight_mean": round(float(sample_weight.mean()), 10),
            "financial_sample_weight_max": round(float(sample_weight.max()), 10),
            "objective_priority": list(FINANCIAL_OBJECTIVES),
            "promotion_eligible": False,
            "quarantine_only": True,
        }
        artifact_path: str | None = None
        artifact_hash: str | None = None
        if write_artifact:
            model_dir = paths.model_dir / run_id
            model_dir.mkdir(parents=True, exist_ok=True)
            artifact = model_dir / f"{backend_id}_candidate_model.json"
            write_json(artifact, candidate)
            artifact_path = str(artifact)
            artifact_hash = file_sha256(artifact)
        return {
            "backend_id": backend_id,
            "status": "trained_quarantine_only",
            "reason": "trained_quarantine_only",
            "artifact_path": artifact_path,
            "artifact_hash": artifact_hash,
            "artifact_written": bool(artifact_path),
            "candidate": candidate,
            "blockers": [],
            "warnings": [],
        }
