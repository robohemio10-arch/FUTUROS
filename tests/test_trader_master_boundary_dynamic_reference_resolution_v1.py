from __future__ import annotations

from pathlib import Path

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    build_legacy_master_boundary_report,
)


ROOT = Path(__file__).resolve().parents[1]
DYNAMIC_TARGETS = (
    "scripts/audit_ai_unified_feature_contract.py",
    "scripts/rebuild_phase5_datasets.py",
    "scripts/vital_bitradex_ocr_canonical_contract_v1.py",
    "smartcrypto/data/trader_master_fingerprint_v2/staging_runner.py",
    "smartcrypto/ml/ai_shadow_threshold_input_builder.py",
    "smartcrypto/ml/unified_feature_contract.py",
    "smartcrypto/research/daily_learning_contracts.py",
    "smartcrypto/research/ocr_master_candle_aligned_oos_research/master_candle_oos_research.py",
    "smartcrypto/research/ocr_master_candle_positive_ev_slice_mining/slice_mining.py",
    "smartcrypto/research/ocr_master_candle_positive_rule_oos_validation/oos_validation.py",
    "smartcrypto/research/ocr_master_candle_shadow_observation_replay/replay.py",
    "smartcrypto/research/paper_master_divergence_oos_real_slice_computation/real_slice_computation.py",
    "smartcrypto/research/paper_master_divergence_research_closeout.py",
)
RESEARCH_CLIS = (
    "scripts/build_ocr_master_candle_positive_ev_slice_mining_v1.py",
    "scripts/build_ocr_master_candle_positive_rule_oos_validation_v1.py",
    "scripts/build_ocr_master_candle_shadow_observation_replay_v1.py",
)


def test_boundary_auditor_has_no_unresolved_dynamic_reference() -> None:
    report = build_legacy_master_boundary_report(project_root=ROOT, write_report=False)

    assert report["critical_count"] == 0
    assert report["dynamic_reference_unresolved_count"] == 0
    assert not any(
        item["classification"] == "dynamic_reference_unresolved"
        for item in report["findings"]
    )


def test_dynamic_targets_do_not_suppress_boundary_findings() -> None:
    for relative_path in DYNAMIC_TARGETS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "noqa: legacy-master" not in source.casefold()
        assert "nosec: legacy-master" not in source.casefold()
        assert "dynamic_reference_unresolved" not in source


def test_research_clis_require_neutral_legacy_dataset_argument() -> None:
    for relative_path in RESEARCH_CLIS:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "--legacy-trade-dataset" in source
        assert "--trades-master" not in source


def test_research_modules_do_not_gain_operational_authority() -> None:
    research_targets = [
        relative_path
        for relative_path in DYNAMIC_TARGETS
        if relative_path.startswith("smartcrypto/research/")
    ]
    for relative_path in research_targets:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "send_order" not in source
        assert "submit_order" not in source
        assert "exchange_private_access=True" not in source
