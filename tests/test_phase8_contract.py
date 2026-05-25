from pathlib import Path

def test_phase8_files_exist_contract():
    expected = [
        "docker/qlib/Dockerfile",
        "docker/qlib/requirements.txt",
        "config/qlib_model.yml",
        "scripts/phase8_preflight.py",
        "scripts/build_qlib_dataset.py",
        "scripts/train_qlib_market_model.py",
        "scripts/export_qlib_predictions.py",
        "scripts/export_qlib_freqtrade_signals.py",
        "paper_controlado_fase_08/RUN_PHASE8_PREFLIGHT.ps1",
    ]
    for file in expected:
        assert Path(file).exists()
