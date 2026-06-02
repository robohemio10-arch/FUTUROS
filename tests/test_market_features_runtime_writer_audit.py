from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_audit_module():
    path = ROOT / "scripts" / "audit_market_features_runtime_writers.py"
    spec = importlib.util.spec_from_file_location("audit_market_features_runtime_writers", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_writers_are_guarded_in_real_repository(tmp_path: Path) -> None:
    module = load_audit_module()

    report = module.audit_market_features_runtime_writers(
        report_path=tmp_path / "market_features_runtime_writer_audit.json",
    )

    assert report["status"] == "ok"
    assert report["prohibited_runtime_writers"] == []
    expected_writers = {
        "scripts/build_market_features.py",
        "scripts/build_phase22_market_features.py",
        "scripts/run_qlib_market_features_refresh.py",
        "scripts/run_qlib_paper_refresh_supervisor.py",
        "scripts/sanitize_market_features_lookahead.py",
        "smartcrypto/data/feature_builder.py",
        "smartcrypto/qlib_engine/market_features_refresh.py",
        "smartcrypto/qlib_engine/paper_refresh_supervisor.py",
    }
    assert expected_writers.issubset(set(report["allowed_runtime_writers"]))


def test_direct_runtime_writer_with_future_ret_is_prohibited(tmp_path: Path) -> None:
    module = load_audit_module()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    bad_writer = scripts_dir / "bad_writer.py"
    bad_writer.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import pandas as pd",
                'OUTPUT = Path("data/features/market_features_60d.parquet")',
                "def main():",
                '    frame = pd.DataFrame({"future_ret_1": [0.01], "close": [100]})',
                "    frame.to_parquet(OUTPUT, index=False)",
            ]
        ),
        encoding="utf-8",
    )

    report = module.audit_market_features_runtime_writers(
        roots=[scripts_dir],
        report_path=tmp_path / "report.json",
        project_root=tmp_path,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "prohibited_runtime_writer_detected"
    assert report["prohibited_runtime_writers"] == ["scripts/bad_writer.py"]


def test_runtime_reader_is_not_reported_as_writer(tmp_path: Path) -> None:
    module = load_audit_module()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    reader = scripts_dir / "reader.py"
    reader.write_text(
        "\n".join(
            [
                "import pandas as pd",
                'FEATURES = "data/features/market_features_60d.parquet"',
                "def load():",
                "    return pd.read_parquet(FEATURES)",
            ]
        ),
        encoding="utf-8",
    )

    report = module.audit_market_features_runtime_writers(
        roots=[scripts_dir],
        report_path=tmp_path / "report.json",
        project_root=tmp_path,
    )

    assert report["status"] == "ok"
    assert report["runtime_reader_files"] == ["scripts/reader.py"]
    assert report["runtime_writer_files"] == []


def test_offline_future_labels_are_classified_separately(tmp_path: Path) -> None:
    module = load_audit_module()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    offline = scripts_dir / "build_training_labels.py"
    offline.write_text(
        "\n".join(
            [
                "import pandas as pd",
                "def build_labels(output):",
                '    labels = pd.DataFrame({"future_ret_3": [0.02]})',
                "    labels.to_parquet(output, index=False)",
            ]
        ),
        encoding="utf-8",
    )

    report = module.audit_market_features_runtime_writers(
        roots=[scripts_dir],
        report_path=tmp_path / "report.json",
        project_root=tmp_path,
    )

    assert report["status"] == "ok"
    assert report["offline_label_files"] == ["scripts/build_training_labels.py"]
    assert report["prohibited_runtime_writers"] == []


def test_report_preserves_paper_shadow_only_flags(tmp_path: Path) -> None:
    module = load_audit_module()

    report = module.audit_market_features_runtime_writers(
        roots=[tmp_path],
        report_path=tmp_path / "report.json",
        project_root=tmp_path,
    )

    assert report["paper_only"] is True
    assert report["runtime_mode"] == "paper"
    assert report["live_trading_enabled"] is False
    assert report["order_submission_enabled"] is False
    assert report["real_order_submission_enabled"] is False
    assert report["exchange_private_access"] is False
