from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ROOT = Path(os.getenv("SMARTCRYPTO_PROJECT_ROOT") or Path.cwd()).resolve()
OUT = ROOT / "data" / "reports" / "ai_shadow_quality_gated_schema_audit_v1.json"

MODEL = ROOT / "data" / "models" / "ai_shadow_filter_extratrees_050.joblib"
OLD_QG = ROOT / "data" / "features" / "training_dataset_quality_gated_binance_1m.parquet"
NEW_TRAINING = ROOT / "data" / "features" / "training_dataset.parquet"
TRADE_ENRICHED = ROOT / "data" / "features" / "trade_enriched.parquet"

OCR_SOURCE = "bitradex_ocr_locked_candidates_20260528_090243"


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    return obj


def extract_features(model: object) -> list[str]:
    if isinstance(model, dict):
        for key in ["feature_columns", "features", "feature_names", "model_features"]:
            if key in model:
                return [str(x) for x in list(model[key])]

        for key in ["model", "estimator", "pipeline"]:
            if key in model:
                nested = extract_features(model[key])
                if nested:
                    return nested

    for attr in ["feature_names_in_", "feature_name_", "feature_names"]:
        if hasattr(model, attr):
            return [str(x) for x in list(getattr(model, attr))]

    if hasattr(model, "steps"):
        for _, step in model.steps:
            nested = extract_features(step)
            if nested:
                return nested

    if hasattr(model, "named_steps"):
        for step in model.named_steps.values():
            nested = extract_features(step)
            if nested:
                return nested

    return []


def summarize_parquet(path: Path, model_features: list[str]) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}

    df = pd.read_parquet(path)
    cols = list(df.columns)

    present = [c for c in model_features if c in df.columns]
    missing = [c for c in model_features if c not in df.columns]

    ocr_rows = None
    if "source_file" in df.columns:
        ocr_rows = int(df["source_file"].astype(str).eq(OCR_SOURCE).sum())

    time_summary = {}
    for col in ["close_ts", "open_ts", "horario_fechamento", "open_time_utc", "close_time_utc"]:
        if col in df.columns:
            ts = pd.to_datetime(df[col], errors="coerce", utc=True)
            if ts.notna().any():
                time_summary[col] = {
                    "min": ts.min().isoformat(),
                    "max": ts.max().isoformat(),
                }

    return {
        "exists": True,
        "path": str(path),
        "rows": int(len(df)),
        "columns_count": int(len(cols)),
        "columns": cols,
        "ocr_rows_by_source_file": ocr_rows,
        "model_feature_coverage": {
            "model_features_total": int(len(model_features)),
            "present": int(len(present)),
            "missing": int(len(missing)),
            "missing_features": missing,
        },
        "time_summary": time_summary,
        "symbol_counts": df["symbol"].astype(str).value_counts(dropna=False).head(20).to_dict() if "symbol" in df.columns else {},
    }


def main() -> None:
    if not MODEL.exists():
        raise FileNotFoundError(f"Modelo n├úo encontrado: {MODEL}")

    model = joblib.load(MODEL)
    model_features = extract_features(model)

    new_df = pd.read_parquet(NEW_TRAINING) if NEW_TRAINING.exists() else pd.DataFrame()

    feature_family_counts = {
        "prior": sum(1 for c in model_features if c.startswith("prior_")),
        "meta": sum(1 for c in model_features if c.startswith("meta_")),
        "v13": sum(1 for c in model_features if c.startswith("v13_")),
        "other": sum(1 for c in model_features if not (c.startswith("prior_") or c.startswith("meta_") or c.startswith("v13_"))),
    }

    possible_direct_name_matches = {}
    for mf in model_features:
        matches = []
        for col in new_df.columns:
            if mf.replace("prior_", "").replace("v13_", "").replace("meta_", "") in col:
                matches.append(col)
        possible_direct_name_matches[mf] = matches[:10]

    summary = {
        "status": "ok",
        "mode": "ai_shadow_quality_gated_schema_audit_v1",
        "model": str(MODEL),
        "model_type": str(type(model)),
        "model_features_count": int(len(model_features)),
        "model_feature_family_counts": feature_family_counts,
        "model_features": model_features,
        "datasets": {
            "old_quality_gated": summarize_parquet(OLD_QG, model_features),
            "new_training_dataset": summarize_parquet(NEW_TRAINING, model_features),
            "trade_enriched": summarize_parquet(TRADE_ENRICHED, model_features),
        },
        "possible_direct_name_matches_from_new_training": possible_direct_name_matches,
        "safety": {
            "writes_training_dataset_quality_gated": False,
            "changes_model": False,
            "sends_orders": False,
            "audit_only": True,
        },
    }

    OUT.write_text(json.dumps(json_safe(summary), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
