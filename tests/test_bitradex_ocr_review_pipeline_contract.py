from __future__ import annotations

import importlib.util
import json
import py_compile
import sys
from pathlib import Path


SCRIPT_PATH = Path("scripts/ocr_bitradex_images_to_review.py")
DOC_PATH = Path("docs/BITRADEX_OCR_REVIEW_PIPELINE.md")


def load_script_module():
    spec = importlib.util.spec_from_file_location("ocr_bitradex_images_to_review", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_exists_compiles_and_has_cli_entrypoint() -> None:
    assert SCRIPT_PATH.exists()
    py_compile.compile(str(SCRIPT_PATH), doraise=True)
    module = load_script_module()
    assert callable(getattr(module, "main", None))
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' in text
    assert "--input-dir" in text
    assert "--output-dir" in text
    assert "--report" in text


def test_script_has_no_live_order_private_flags_or_env_mutation() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "ccxt",
        "create_order",
        "submit_order",
        "fetch_balance",
        "LIVE_ENABLED=true",
        "ORDER_SUBMISSION_ENABLED=true",
        "REAL_ORDER_SUBMISSION_ENABLED=true",
        "dotenv",
        ".env",
    ]
    assert all(token not in text for token in forbidden)


def test_script_does_not_import_15s_shadow_pipeline() -> None:
    text = SCRIPT_PATH.read_text(encoding="utf-8")
    forbidden = [
        "audit_bitradex_15s_close_only",
        "build_15s_microstructure_shadow_features",
        "join_training_dataset_with_15s_shadow_features",
    ]
    assert all(token not in text for token in forbidden)


def test_dry_run_accepts_tmp_path_and_does_not_write_data(tmp_path) -> None:
    module = load_script_module()
    input_dir = tmp_path / "local_images"
    output_dir = tmp_path / "ocr_review"
    report_path = tmp_path / "reports" / "ocr_report.json"
    input_dir.mkdir()
    (input_dir / "Screenshot_001.jpg").write_bytes(b"not-a-real-image")

    module.main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--report",
            str(report_path),
            "--dry-run",
            "--no-xlsx",
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["review_only"] is True
    assert report["dry_run"] is True
    assert report["images_found"] == 1
    assert report["review_status_counts"]["DISCOVERED_DRY_RUN"] == 1
    assert report["safety"]["writes_trades_master"] is False
    assert report["safety"]["changes_training_dataset"] is False
    assert report["outputs"]["review_xlsx"] is None
    assert json.dumps(report, sort_keys=True)
    assert not (tmp_path / "data").exists()


def test_empty_input_dir_does_not_require_real_bitradex_or_screenshots(tmp_path) -> None:
    module = load_script_module()
    input_dir = tmp_path / "empty"
    output_dir = tmp_path / "out"
    report_path = tmp_path / "report.json"
    input_dir.mkdir()

    module.main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--report",
            str(report_path),
            "--dry-run",
            "--no-xlsx",
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["images_found"] == 0
    assert report["rows"] == 0
    assert not Path("BITRADEX").exists()


def test_ocr_dependency_absence_is_auditable_with_mocked_missing_engine(tmp_path, monkeypatch) -> None:
    module = load_script_module()
    input_dir = tmp_path / "images"
    output_dir = tmp_path / "out"
    report_path = tmp_path / "report.json"
    input_dir.mkdir()
    (input_dir / "Screenshot_001.jpg").write_bytes(b"not-a-real-image")

    monkeypatch.setattr(
        module,
        "load_ocr_dependencies",
        lambda: (_ for _ in ()).throw(module.OCRDependencyError("ocr_engine_unavailable:test")),
    )
    module.main(
        [
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--report",
            str(report_path),
            "--no-xlsx",
        ]
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ocr_dependency_error"] == "ocr_engine_unavailable:test"
    assert report["review_status_counts"]["OCR_ENGINE_UNAVAILABLE"] == 1


def test_documentation_explains_manual_review_and_no_image_versioning() -> None:
    assert DOC_PATH.exists()
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "screenshots nao sao versionados" in text
    assert "revisao manual" in text
    assert "nao altera dataset oficial" in text or "nao atualiza automaticamente" in text
    assert "nao libera live trading" in text
