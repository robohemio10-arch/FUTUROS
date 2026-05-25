from pathlib import Path


def test_phase7_files_exist():
    assert Path("scripts/collect_freqtrade_paper_history.py").exists()
    assert Path("smartcrypto/data/freqtrade_history.py").exists()
