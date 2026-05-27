from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(".")

DEFAULT_BASE_DATASET = ROOT / "data" / "features" / "training_dataset_quality_gated_binance_1m.parquet"
DEFAULT_SHADOW_FEATURES = ROOT / "data" / "features" / "bitradex_15s_microstructure_shadow_features.parquet"

OUT_FEATURES_DIR = ROOT / "data" / "features"
OUT_REPORTS_DIR = ROOT / "data" / "reports"


BLOCKED_TOKENS = [
    "high",
    "low",
    "range",
    "wick",
    "ohlc",
]


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
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def read_any(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)

    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)

    raise RuntimeError(f"Formato não suportado: {path}")


def normalize_symbol(value: object) -> str:
    return (
        str(value)
        .upper()
        .replace("/", "")
        .replace("_", "")
        .replace("-", "")
        .replace(":USDT", "")
        .strip()
    )


def detect_time_column(df: pd.DataFrame, explicit: str | None) -> str:
    if explicit:
        if explicit not in df.columns:
            raise RuntimeError(f"Coluna de tempo explicitada não existe: {explicit}")
        return explicit

    candidates = [
        "open_time_utc",
        "entry_time_utc",
        "trade_time_utc",
        "timestamp",
        "datetime",
        "time",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    raise RuntimeError(
        "Não consegui detectar coluna de tempo no dataset base. "
        f"Colunas disponíveis: {list(df.columns)}"
    )


def detect_symbol_column(df: pd.DataFrame, explicit: str | None) -> str:
    if explicit:
        if explicit not in df.columns:
            raise RuntimeError(f"Coluna de símbolo explicitada não existe: {explicit}")
        return explicit

    candidates = [
        "symbol",
        "pair",
        "market",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    raise RuntimeError(
        "Não consegui detectar coluna de símbolo no dataset base. "
        f"Colunas disponíveis: {list(df.columns)}"
    )


def validate_shadow_features(features: pd.DataFrame) -> dict:
    required = [
        "symbol",
        "join_time_utc",
        "usable_from_utc",
        "allow_live_execution",
        "allow_shadow_only",
    ]

    missing = [col for col in required if col not in features.columns]

    if missing:
        raise RuntimeError(f"Shadow features sem colunas obrigatórias: {missing}")

    forbidden = []

    for col in features.columns:
        col_l = col.lower()

        if col_l.startswith("micro15s_") and any(token in col_l for token in BLOCKED_TOKENS):
            forbidden.append(col)

    if forbidden:
        raise RuntimeError(
            "Dataset shadow contém colunas proibidas para política close-only: "
            f"{forbidden}"
        )

    live_execution_values = features["allow_live_execution"].dropna().unique().tolist()
    shadow_values = features["allow_shadow_only"].dropna().unique().tolist()

    if any(bool(v) for v in live_execution_values):
        raise RuntimeError("allow_live_execution contém valor True. Política violada.")

    if not all(bool(v) for v in shadow_values):
        raise RuntimeError("allow_shadow_only contém valor False ou inválido. Política violada.")

    return {
        "status": "ok",
        "required_columns_present": True,
        "forbidden_columns": [],
        "allow_live_execution_unique": live_execution_values,
        "allow_shadow_only_unique": shadow_values,
    }


def prepare_base(
    base: pd.DataFrame,
    time_col: str,
    symbol_col: str,
) -> pd.DataFrame:
    out = base.copy()

    out["_base_row_id"] = np.arange(len(out), dtype=np.int64)
    out["_base_symbol_norm"] = out[symbol_col].map(normalize_symbol)
    out["_base_time_utc"] = pd.to_datetime(out[time_col], errors="coerce", utc=True)

    out = out.dropna(subset=["_base_time_utc"])
    out = out[out["_base_symbol_norm"].isin(["BTCUSDT", "ETHUSDT"])].copy()

    out = out.sort_values(["_base_symbol_norm", "_base_time_utc", "_base_row_id"]).reset_index(drop=True)

    return out


def prepare_shadow(features: pd.DataFrame) -> pd.DataFrame:
    out = features.copy()

    out["_shadow_symbol_norm"] = out["symbol"].map(normalize_symbol)
    out["_shadow_join_time_utc"] = pd.to_datetime(out["join_time_utc"], errors="coerce", utc=True)
    out["_shadow_usable_from_utc"] = pd.to_datetime(out["usable_from_utc"], errors="coerce", utc=True)

    out = out.dropna(subset=["_shadow_join_time_utc", "_shadow_usable_from_utc"])
    out = out[out["_shadow_symbol_norm"].isin(["BTCUSDT", "ETHUSDT"])].copy()

    if not out["_shadow_join_time_utc"].equals(out["_shadow_usable_from_utc"]):
        # Não é erro fatal; o join sempre usa usable_from_utc.
        pass

    allowed_cols = [
        col for col in out.columns
        if (
            col.startswith("micro15s_")
            or col in [
                "symbol",
                "feature_minute_utc",
                "join_time_utc",
                "usable_from_utc",
                "timestamp",
                "captured_at",
                "feature_source",
                "feature_policy",
                "allow_live_execution",
                "allow_shadow_only",
                "_shadow_symbol_norm",
                "_shadow_join_time_utc",
                "_shadow_usable_from_utc",
            ]
        )
    ]

    out = out[allowed_cols].copy()
    out = out.sort_values(["_shadow_symbol_norm", "_shadow_usable_from_utc"]).reset_index(drop=True)

    return out


def asof_join_by_symbol(
    base: pd.DataFrame,
    shadow: pd.DataFrame,
    max_feature_age_minutes: float,
) -> pd.DataFrame:
    joined_frames = []

    for symbol in sorted(base["_base_symbol_norm"].dropna().unique()):
        left = base[base["_base_symbol_norm"] == symbol].copy()
        right = shadow[shadow["_shadow_symbol_norm"] == symbol].copy()

        left = left.sort_values("_base_time_utc")
        right = right.sort_values("_shadow_usable_from_utc")

        if right.empty:
            left["shadow15s_feature_available"] = False
            left["shadow15s_feature_age_seconds"] = np.nan
            joined_frames.append(left)
            continue

        merged = pd.merge_asof(
            left,
            right,
            left_on="_base_time_utc",
            right_on="_shadow_usable_from_utc",
            direction="backward",
            allow_exact_matches=True,
        )

        merged["shadow15s_feature_age_seconds"] = (
            merged["_base_time_utc"] - merged["_shadow_usable_from_utc"]
        ).dt.total_seconds()

        max_age_seconds = max_feature_age_minutes * 60.0

        valid = (
            merged["_shadow_usable_from_utc"].notna()
            & merged["shadow15s_feature_age_seconds"].between(0, max_age_seconds)
        )

        merged["shadow15s_feature_available"] = valid

        shadow_cols = [
            col for col in merged.columns
            if col.startswith("micro15s_")
            or col in [
                "feature_minute_utc",
                "join_time_utc",
                "usable_from_utc",
                "timestamp",
                "captured_at",
                "feature_source",
                "feature_policy",
                "allow_live_execution",
                "allow_shadow_only",
            ]
        ]

        for col in shadow_cols:
            merged.loc[~valid, col] = np.nan

        joined_frames.append(merged)

    if not joined_frames:
        return base.copy()

    out = pd.concat(joined_frames, ignore_index=True)
    out = out.sort_values("_base_row_id").reset_index(drop=True)

    return out


def make_manifest(df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for col in df.columns:
        col_l = col.lower()

        if col.startswith("micro15s_"):
            source = "bitradex_15s_close_only_shadow"
            allowed_shadow = True
            allowed_live = False
        elif col in [
            "shadow15s_feature_available",
            "shadow15s_feature_age_seconds",
            "feature_minute_utc",
            "join_time_utc",
            "usable_from_utc",
            "feature_source",
            "feature_policy",
            "allow_live_execution",
            "allow_shadow_only",
        ]:
            source = "shadow_metadata"
            allowed_shadow = True
            allowed_live = False
        else:
            source = "base_dataset"
            allowed_shadow = True
            allowed_live = False

        blocked = col_l.startswith("micro15s_") and any(token in col_l for token in BLOCKED_TOKENS)

        rows.append({
            "column": col,
            "source": source,
            "blocked_by_close_only_policy": bool(blocked),
            "allowed_for_shadow": bool(allowed_shadow and not blocked),
            "allowed_for_live_execution": bool(allowed_live),
        })

    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=str, default=str(DEFAULT_BASE_DATASET))
    parser.add_argument("--shadow", type=str, default=str(DEFAULT_SHADOW_FEATURES))
    parser.add_argument("--time-col", type=str, default=None)
    parser.add_argument("--symbol-col", type=str, default=None)
    parser.add_argument("--max-feature-age-minutes", type=float, default=5.0)
    parser.add_argument(
        "--output-parquet",
        type=str,
        default=str(OUT_FEATURES_DIR / "training_dataset_quality_gated_binance_1m_plus_15s_shadow.parquet"),
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=str(OUT_FEATURES_DIR / "training_dataset_quality_gated_binance_1m_plus_15s_shadow.csv"),
    )
    parser.add_argument(
        "--matched-csv",
        type=str,
        default=str(OUT_REPORTS_DIR / "training_dataset_15s_shadow_join_matched_rows.csv"),
    )
    parser.add_argument(
        "--unmatched-csv",
        type=str,
        default=str(OUT_REPORTS_DIR / "training_dataset_15s_shadow_join_unmatched_sample.csv"),
    )
    parser.add_argument(
        "--manifest-csv",
        type=str,
        default=str(OUT_REPORTS_DIR / "training_dataset_15s_shadow_join_manifest.csv"),
    )
    parser.add_argument(
        "--summary-json",
        type=str,
        default=str(OUT_REPORTS_DIR / "training_dataset_15s_shadow_join_summary.json"),
    )
    args = parser.parse_args(argv)

    base_path = Path(args.base)
    shadow_path = Path(args.shadow)

    base_raw = read_any(base_path)
    shadow_raw = read_any(shadow_path)

    time_col = detect_time_column(base_raw, args.time_col)
    symbol_col = detect_symbol_column(base_raw, args.symbol_col)

    shadow_validation = validate_shadow_features(shadow_raw)

    base = prepare_base(base_raw, time_col=time_col, symbol_col=symbol_col)
    shadow = prepare_shadow(shadow_raw)

    joined = asof_join_by_symbol(
        base=base,
        shadow=shadow,
        max_feature_age_minutes=args.max_feature_age_minutes,
    )

    matched = joined[joined["shadow15s_feature_available"] == True].copy()
    unmatched = joined[joined["shadow15s_feature_available"] != True].copy()

    manifest = make_manifest(joined)
    blocked = manifest[manifest["blocked_by_close_only_policy"] == True]

    if len(blocked):
        raise RuntimeError(
            "Colunas shadow bloqueadas encontradas após join: "
            f"{blocked['column'].tolist()}"
        )

    output_parquet = Path(args.output_parquet)
    output_csv = Path(args.output_csv)
    matched_csv = Path(args.matched_csv)
    unmatched_csv = Path(args.unmatched_csv)
    manifest_csv = Path(args.manifest_csv)
    summary_json = Path(args.summary_json)
    for output in (output_parquet, output_csv, matched_csv, unmatched_csv, manifest_csv, summary_json):
        output.parent.mkdir(parents=True, exist_ok=True)

    # Remove colunas técnicas internas antes de salvar dataset final.
    drop_internal = [
        "_base_row_id",
        "_base_symbol_norm",
        "_base_time_utc",
        "_shadow_symbol_norm",
        "_shadow_join_time_utc",
        "_shadow_usable_from_utc",
    ]

    final = joined.drop(columns=[col for col in drop_internal if col in joined.columns], errors="ignore")

    final.to_csv(output_csv, index=False, encoding="utf-8-sig")

    parquet_written = False

    try:
        final.to_parquet(output_parquet, index=False)
        parquet_written = True
    except Exception:
        parquet_written = False

    matched.drop(columns=[col for col in drop_internal if col in matched.columns], errors="ignore").to_csv(
        matched_csv,
        index=False,
        encoding="utf-8-sig",
    )

    unmatched.drop(columns=[col for col in drop_internal if col in unmatched.columns], errors="ignore").head(5000).to_csv(
        unmatched_csv,
        index=False,
        encoding="utf-8-sig",
    )

    manifest.to_csv(manifest_csv, index=False, encoding="utf-8-sig")

    summary = {
        "status": "ok",
        "mode": "join_training_dataset_with_15s_shadow_features",
        "safety": {
            "sends_orders": False,
            "changes_risk": False,
            "live_trading": False,
            "shadow_only": True,
            "official_ai_dataset_modified": False,
            "base_dataset_overwritten": False,
        },
        "inputs": {
            "base": str(base_path),
            "shadow": str(shadow_path),
            "base_time_col": time_col,
            "base_symbol_col": symbol_col,
        },
        "parameters": {
            "max_feature_age_minutes": float(args.max_feature_age_minutes),
        },
        "shadow_validation": shadow_validation,
        "rows": {
            "base_raw": int(len(base_raw)),
            "base_prepared": int(len(base)),
            "shadow_raw": int(len(shadow_raw)),
            "shadow_prepared": int(len(shadow)),
            "final": int(len(final)),
            "matched": int(len(matched)),
            "unmatched": int(len(unmatched)),
            "match_ratio": float(len(matched) / len(final)) if len(final) else 0.0,
        },
        "matched_by_symbol": (
            matched["_base_symbol_norm"].value_counts().to_dict()
            if len(matched) and "_base_symbol_norm" in matched.columns
            else {}
        ),
        "time_ranges": {
            "base_min_time": base["_base_time_utc"].min().isoformat() if len(base) else None,
            "base_max_time": base["_base_time_utc"].max().isoformat() if len(base) else None,
            "shadow_min_usable_from": shadow["_shadow_usable_from_utc"].min().isoformat() if len(shadow) else None,
            "shadow_max_usable_from": shadow["_shadow_usable_from_utc"].max().isoformat() if len(shadow) else None,
        },
        "policy": {
            "allowed_shadow_features": [
                "micro15s_close",
                "micro15s_returns",
                "micro15s_micro_momentum",
                "micro15s_micro_volatility_from_close",
            ],
            "blocked_shadow_features": [
                "micro15s_high",
                "micro15s_low",
                "micro15s_range",
                "micro15s_wicks",
                "micro15s_ohlc_patterns",
            ],
        },
        "outputs": {
            "output_parquet": str(output_parquet) if parquet_written else None,
            "output_csv": str(output_csv),
            "matched_csv": str(matched_csv),
            "unmatched_sample_csv": str(unmatched_csv),
            "manifest_csv": str(manifest_csv),
            "summary_json": str(summary_json),
        },
    }

    summary_json.write_text(
        json.dumps(json_safe(summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(json_safe(summary), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
