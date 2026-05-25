from smartcrypto.ml.predict_scores import export_predictions


if __name__ == "__main__":
    export_predictions(
        market_features_path="data/features/market_features_60d.parquet",
        model_path="data/models/baseline_model.joblib",
        output_path="data/predictions/latest_predictions.parquet",
    )
