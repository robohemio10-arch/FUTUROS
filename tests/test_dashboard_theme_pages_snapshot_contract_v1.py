from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "smartcrypto" / "dashboard" / "pages"
COMPONENTS = ROOT / "smartcrypto" / "dashboard" / "components"


def test_sensitive_semantic_sections_remain_present() -> None:
    controls = (PAGES / "06_active_controls.py").read_text(encoding="utf-8")
    quant = (PAGES / "07_quantitative_reports.py").read_text(encoding="utf-8")
    alerts = (PAGES / "08_alerts_messaging.py").read_text(encoding="utf-8")
    readiness = (COMPONENTS / "readiness_gates.py").read_text(encoding="utf-8")
    decision = (COMPONENTS / "decision_trace.py").read_text(encoding="utf-8")
    pipeline = (COMPONENTS / "dataset_pipeline.py").read_text(encoding="utf-8")
    alert_stubs = (COMPONENTS / "alert_stubs.py").read_text(encoding="utf-8")

    assert "Readiness & Gates" in readiness
    assert "HARD_BLOCKED" in controls
    assert "Financial Event Log / Decision Trace" in decision
    assert "Dataset / OCR / Training Pipeline Status" in pipeline
    assert "render_financial_event_log_decision_trace" in quant
    assert "render_dataset_ocr_training_pipeline_status" in quant
    assert "STUB ONLY - NO TELEGRAM/NTFY SEND" in alert_stubs
    assert "dashboard_alerts_messaging_snapshot.json" in alerts


def test_all_pages_keep_readonly_snapshot_loader_contract() -> None:
    for path in PAGES.glob("[0-9][0-9]_*.py"):
        source = path.read_text(encoding="utf-8")
        assert "load_page_snapshot" in source
        assert "render_snapshot_page" in source
        assert "render_chrome=False" in source
