from pathlib import Path


def test_phase6_files_exist():
    assert Path("config/market_model.yml").exists()
    assert Path("scripts/train_market_direction_model.py").exists()
    assert Path("scripts/export_market_predictions.py").exists()
    assert Path("scripts/export_market_freqtrade_signals.py").exists()
