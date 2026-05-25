from smartcrypto.ml.train_model import train_model


if __name__ == "__main__":
    train_model(
        trade_enriched_path="data/features/trade_enriched.parquet",
        model_path="data/models/baseline_model.joblib",
        metrics_path="data/models/baseline_metrics.json",
    )
