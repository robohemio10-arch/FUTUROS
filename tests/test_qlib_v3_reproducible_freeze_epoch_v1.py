from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import zipfile
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smartcrypto.learning.paper_autolearning import (
    qlib_market_context_economic_challenger as base,
)
from smartcrypto.learning.paper_autolearning.qlib_v3_reproducible_freeze_epoch import (
    ACTIVATION_SCHEMA_VERSION,
    FreezeV3Error,
    _activation_identity,
    _canonical_json_bytes,
    _cross_rebuild_match,
    _float_hex,
    _git_lineage_contract,
    _model_semantic_fingerprint,
    _safe_extract_zip,
    _sha256_json,
    validate_v3_prospective_event,
)


def test_canonical_json_and_float_representation_are_deterministic() -> None:
    left = {"b": 2, "a": [1, 3]}
    right = {"a": [1, 3], "b": 2}
    assert _canonical_json_bytes(left) == _canonical_json_bytes(right)
    assert _sha256_json(left) == _sha256_json(right)
    assert _float_hex(0.1, field="x") == float(0.1).hex()


def test_cross_rebuild_requires_semantic_equality_but_reports_bytes_separately() -> None:
    common = {
        "match": True,
        "fit_trade_count": 10,
        "fit_trade_ids_sha256": "a",
        "calibration_trade_count": 4,
        "calibration_trade_ids_sha256": "b",
        "dataset_row_count": 14,
        "dataset_fingerprint": "c",
        "frozen_matrix_sha256": "d",
        "median_vector_sha256": "e",
        "feature_count": 3,
        "feature_columns_sha256": "f",
        "model_semantic_fingerprint": "g",
        "treatment_semantic_fingerprint": "h",
        "calibration_sha256": "i",
        "calibration_score_vector_sha256": "j",
        "threshold_float64": float(1.25).hex(),
        "policy_sha256": "k",
    }
    first = {**common, "model_artifact_sha256_rebuilt": "bytes-1"}
    second = {**common, "model_artifact_sha256_rebuilt": "bytes-2"}
    assert _cross_rebuild_match(first, second) is True


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "bad")
    with pytest.raises(FreezeV3Error, match="implementation_archive_path_traversal"):
        _safe_extract_zip(archive, tmp_path / "extract")


def test_git_lineage_requires_clean_committed_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    for name in (
        "pyproject.toml",
        "requirements-qlib.lock",
        "requirements-runtime.lock",
        "requirements-dev.lock",
    ):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    lineage = _git_lineage_contract(repo)
    assert lineage["git_commit_sha"]
    assert lineage["git_tree_sha"]
    assert len(lineage["lockfiles"]) == 4

    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(FreezeV3Error, match="git_worktree_must_be_clean_before_freeze"):
        _git_lineage_contract(repo)


def test_v3_event_admission_requires_activation_identity_and_future_boundary() -> None:
    freeze = {
        "epoch_id": "qlib-v3-test",
        "epoch_version": "v3",
        "freeze_v3_sha256": "f" * 64,
        "v3_freeze_reproducible": True,
    }
    activation = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "epoch_id": "qlib-v3-test",
        "epoch_version": "v3",
        "freeze_v3_sha256": "f" * 64,
        "activated_at_utc": "2026-09-11T12:00:00+00:00",
        "prospective_start_utc": "2026-09-11T12:01:00+00:00",
        "activation_delay_seconds": 60,
        "process_isolated_rebuilds": True,
    }
    activation["activation_sha256"] = _activation_identity(activation)

    before = validate_v3_prospective_event(
        freeze_manifest=freeze,
        activation_manifest=activation,
        event={
            "epoch_id": "qlib-v3-test",
            "epoch_version": "v3",
            "observed_at_utc": "2026-09-11T12:00:59+00:00",
        },
    )
    assert before["event_admitted"] is False
    assert "event_precedes_v3_prospective_start" in before["blockers"]

    after = validate_v3_prospective_event(
        freeze_manifest=freeze,
        activation_manifest=activation,
        event={
            "epoch_id": "qlib-v3-test",
            "epoch_version": "v3",
            "observed_at_utc": "2026-09-11T12:01:00+00:00",
        },
    )
    assert after["event_admitted"] is True
    assert after["backfill_performed"] is False


def test_training_contract_remains_exactly_v2_compatible() -> None:
    contract = base.native_qlib_lgb_training_contract()
    assert contract == {
        "loss": "mse",
        "early_stopping_rounds": 20,
        "num_boost_round": 160,
        "learning_rate": 0.03,
        "max_depth": 3,
        "num_leaves": 15,
        "min_data_in_leaf": 25,
        "feature_fraction": 0.80,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "lambda_l1": 0.10,
        "lambda_l2": 0.50,
        "seed": 42,
        "bagging_seed": 42,
        "feature_fraction_seed": 42,
        "data_random_seed": 42,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "model_validation_fraction": 0.20,
        "min_model_validation_trades": 20,
        "min_model_train_trades": 70,
    }


def test_pit_alignment_normalizes_mixed_datetime_resolution_for_pandas3() -> None:
    open_time = pd.Timestamp("2026-06-01T00:10:00.123456Z").to_pydatetime()
    outcomes = [
        {
            "__symbol": "ETHUSDT",
            "__open_time": open_time,
            "notional": 100.0,
        }
    ]

    market_row: dict[str, object] = {
        "symbol": "ETHUSDT",
        "ts": pd.Timestamp("2026-06-01T00:05:00Z"),
        "available_at_utc": pd.Timestamp("2026-06-01T00:10:00Z"),
    }
    market_row.update({column: 1.0 for column in base.MARKET_SOURCE_COLUMNS})
    market = pd.DataFrame([market_row])
    market["ts"] = pd.to_datetime(
        market["ts"],
        utc=True,
        errors="raise",
    ).astype("datetime64[ns, UTC]")
    market["available_at_utc"] = pd.to_datetime(
        market["available_at_utc"],
        utc=True,
        errors="raise",
    ).astype("datetime64[ns, UTC]")

    ready, report = base._align_point_in_time_market_features(outcomes, market)

    assert report["status"] == "ok"
    assert report["input_trade_count"] == 1
    assert report["ready_trade_count"] == 1
    assert report["coverage"] == 1.0
    assert len(ready) == 1
    assert ready[0]["__market_ready"] is True
    assert ready[0]["__market_feature_available_at"] == pd.Timestamp(
        "2026-06-01T00:10:00Z"
    ).to_pydatetime()
    assert ready[0]["__market_feature_age_seconds"] == pytest.approx(0.123456)


def test_v2_predictor_wrapper_is_semantically_transparent(monkeypatch: pytest.MonkeyPatch) -> None:
    train_x = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    train_y = pd.Series([1.0, 2.0, 3.0], dtype=float)
    calibration_x = pd.DataFrame({"x": [4.0, 5.0]})
    test_x = pd.DataFrame({"x": [6.0, 7.0]})

    expected_calibration = np.asarray([0.4, 0.5], dtype=float)
    expected_test = np.asarray([0.6, 0.7], dtype=float)

    def trainer(*args: object, **kwargs: object) -> base.NativeQlibLgbFitResult:
        del args, kwargs
        return base.NativeQlibLgbFitResult(
            calibration_scores=expected_calibration,
            test_scores=expected_test,
            model_text="tree\n",
            best_iteration=7,
        )

    @contextlib.contextmanager
    def fake_context():
        yield trainer, {"framework": "test"}

    monkeypatch.setattr(base, "_native_qlib_lgb_trainer_context", fake_context)
    with base._native_qlib_lgb_predictor_context() as (predictor, metadata):
        calibration, test = predictor(
            train_x,
            train_y,
            calibration_x,
            test_x,
            fold_id="fold_01",
        )
    assert np.array_equal(calibration, expected_calibration)
    assert np.array_equal(test, expected_test)
    assert metadata["framework"] == "test"


@pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None, reason="lightgbm unavailable")
def test_model_semantic_fingerprint_ignores_text_line_ending_only() -> None:
    import lightgbm as lgb

    x = np.asarray([[0.0], [1.0], [2.0], [3.0], [4.0]], dtype=float)
    y = np.asarray([0.0, 1.0, 2.0, 3.0, 4.0], dtype=float)
    booster = lgb.train(
        {
            "objective": "regression",
            "verbosity": -1,
            "seed": 42,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": 1,
        },
        lgb.Dataset(x, label=y),
        num_boost_round=3,
    )
    text = booster.model_to_string()
    assert _model_semantic_fingerprint(text) == _model_semantic_fingerprint(
        text.rstrip("\n") + "\n"
    )




def _transaction_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    train_error: Exception | None = None,
    artifact_error: Exception | None = None,
    checkpoint_error: Exception | None = None,
    report_error: Exception | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )

    state: dict[str, object] = {"reports": []}
    descriptor = {
        "logical_id": "outcome_events",
        "sha256": "1" * 64,
        "bytes": 1,
        "row_count": 1,
        "column_count": 1,
        "schema_sha256": "2" * 64,
        "min_timestamp_utc": "2026-09-11T00:00:00+00:00",
        "max_timestamp_utc": "2026-09-11T00:00:00+00:00",
        "timestamp_contract": "test",
    }
    descriptor_market = {**descriptor, "logical_id": "market_features_60d", "sha256": "3" * 64}

    def fake_stable_copy_source(
        source: Path,
        destination: Path,
        *,
        logical_id: str,
        clock: object,
    ) -> object:
        del source, clock
        destination.mkdir(parents=True, exist_ok=True)
        frozen = destination / f"{logical_id}.parquet"
        frozen.write_bytes(b"frozen")
        return type(
            "Snapshot",
            (),
            {
                "logical_id": logical_id,
                "source_path": tmp_path / f"{logical_id}.source",
                "frozen_path": frozen,
                "sha256": "4" * 64,
                "bytes": len(b"frozen"),
                "source_modified_at_utc": "2026-09-11T00:00:00+00:00",
                "captured_at_utc": "2026-09-11T00:00:00+00:00",
            },
        )()

    def fake_source_descriptor(snapshot: object, *, epoch_dir: Path) -> dict[str, object]:
        del epoch_dir
        logical_id = str(getattr(snapshot, "logical_id"))
        return dict(descriptor if logical_id == "outcome_events" else descriptor_market)

    def fake_write_new_json(path: Path, payload: object) -> str:
        del payload
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return "5" * 64

    def fake_write_source_archive(
        project_root: Path,
        epoch_dir: Path,
        lineage: object,
        *,
        prepared_source_archive: Path | None,
    ) -> dict[str, object]:
        del project_root, lineage, prepared_source_archive
        implementation = epoch_dir / "implementation"
        implementation.mkdir(parents=True, exist_ok=True)
        (implementation / "lineage.json").write_text("{}\n", encoding="utf-8")
        (implementation / "source_tree.zip").write_bytes(b"archive")
        return {
            "git_tree_sha": "a" * 40,
            "git_commit_sha": "b" * 40,
            "source_archive_sha256": "6" * 64,
            "lineage_sha256": "7" * 64,
        }

    def fake_train_epoch(*args: object, **kwargs: object) -> object:
        del args, kwargs
        if train_error is not None:
            raise train_error
        return object()

    def fake_training_artifacts(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        if artifact_error is not None:
            raise artifact_error
        return {
            "model_artifact_byte_reproducible": True,
            "artifacts": {},
        }

    def fake_artifact_entry(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {"sha256": "8" * 64, "bytes": 1}

    rebuild = {
        "match": True,
        "model_artifact_byte_reproducible": True,
        "process_isolated": True,
    }

    def fake_rebuild_pair(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object], bool]:
        del args, kwargs
        return dict(rebuild), dict(rebuild), True

    def fake_checkpoint(
        epoch_dir: Path,
        checkpoint_root: Path,
        freeze_sha: str,
    ) -> Path:
        del epoch_dir, freeze_sha
        if checkpoint_error is not None:
            raise checkpoint_error
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        checkpoint = checkpoint_root / "checkpoint"
        checkpoint.mkdir(exist_ok=True)
        return checkpoint

    def fake_report(project_root: Path, report_path: Path, payload: Mapping[str, object]) -> None:
        del project_root, report_path
        if report_error is not None:
            raise report_error
        reports = state["reports"]
        assert isinstance(reports, list)
        reports.append(dict(payload))

    monkeypatch.setattr(v3, "_stable_copy_source", fake_stable_copy_source)
    monkeypatch.setattr(v3, "_source_descriptor", fake_source_descriptor)
    monkeypatch.setattr(v3, "_write_new_json", fake_write_new_json)
    monkeypatch.setattr(v3, "_write_source_archive", fake_write_source_archive)
    monkeypatch.setattr(v3, "_derive_initial_cohorts", lambda **kwargs: (object(), {"coverage": 1.0}))
    monkeypatch.setattr(v3, "_train_epoch", fake_train_epoch)
    monkeypatch.setattr(v3, "_write_training_artifacts", fake_training_artifacts)
    monkeypatch.setattr(v3, "_artifact_entry", fake_artifact_entry)
    monkeypatch.setattr(v3, "_independent_rebuild_pair", fake_rebuild_pair)
    monkeypatch.setattr(v3, "_checkpoint_tree_valid", lambda *args, **kwargs: True)
    monkeypatch.setattr(v3, "_copy_checkpoint_verified", fake_checkpoint)
    monkeypatch.setattr(v3, "_write_report", fake_report)

    paths: dict[str, object] = {
        "project_root": tmp_path,
        "outcome_source": tmp_path / "outcomes.parquet",
        "market_source": tmp_path / "market.parquet",
        "artifact_root": tmp_path / "data/research/qlib_v3",
        "report_path": tmp_path / "data/reports/qlib_v3/report.json",
        "checkpoint_root": tmp_path / "external-checkpoints",
    }
    return state, paths


def _run_transaction_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    train_error: Exception | None = None,
    artifact_error: Exception | None = None,
    checkpoint_error: Exception | None = None,
    report_error: Exception | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )

    state, paths = _transaction_fixture(
        monkeypatch,
        tmp_path,
        train_error=train_error,
        artifact_error=artifact_error,
        checkpoint_error=checkpoint_error,
        report_error=report_error,
    )
    result = v3._materialize_with_trainer(
        project_root=paths["project_root"],
        outcome_source=paths["outcome_source"],
        market_source=paths["market_source"],
        artifact_root=paths["artifact_root"],
        report_path=paths["report_path"],
        checkpoint_root=paths["checkpoint_root"],
        stress_bps=5.0,
        trainer=lambda *args, **kwargs: None,
        trainer_metadata={"framework": "test"},
        environment_contract={"deterministic_sha256": "9" * 64},
        lineage={"git_tree_sha": "a" * 40},
        prepared_source_archive=None,
        clock=lambda: pd.Timestamp("2026-09-11T12:00:00Z").to_pydatetime(),
        process_isolated_rebuilds=True,
    )
    return result, state, paths



def test_transaction_cleanup_after_final_path_before_publish_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )

    _, paths = _transaction_fixture(
        monkeypatch,
        tmp_path,
        artifact_error=v3.FreezeV3Error("injected_artifact_failure"),
    )
    with pytest.raises(v3.FreezeV3Error, match="injected_artifact_failure"):
        v3._materialize_with_trainer(
            project_root=paths["project_root"],
            outcome_source=paths["outcome_source"],
            market_source=paths["market_source"],
            artifact_root=paths["artifact_root"],
            report_path=paths["report_path"],
            checkpoint_root=paths["checkpoint_root"],
            stress_bps=5.0,
            trainer=lambda *args, **kwargs: None,
            trainer_metadata={"framework": "test"},
            environment_contract={"deterministic_sha256": "9" * 64},
            lineage={"git_tree_sha": "a" * 40},
            prepared_source_archive=None,
            clock=lambda: pd.Timestamp("2026-09-11T12:00:00Z").to_pydatetime(),
            process_isolated_rebuilds=True,
        )
    artifact_root = paths["artifact_root"]
    assert isinstance(artifact_root, Path)
    assert list(artifact_root.glob(".qlib-v3-freeze-*")) == []
    assert not (artifact_root / "epochs").exists()

def test_transaction_cleanup_removes_staging_after_training_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )

    _, paths = _transaction_fixture(
        monkeypatch,
        tmp_path,
        train_error=v3.FreezeV3Error("injected_training_failure"),
    )
    with pytest.raises(v3.FreezeV3Error, match="injected_training_failure"):
        v3._materialize_with_trainer(
            project_root=paths["project_root"],
            outcome_source=paths["outcome_source"],
            market_source=paths["market_source"],
            artifact_root=paths["artifact_root"],
            report_path=paths["report_path"],
            checkpoint_root=paths["checkpoint_root"],
            stress_bps=5.0,
            trainer=lambda *args, **kwargs: None,
            trainer_metadata={"framework": "test"},
            environment_contract={"deterministic_sha256": "9" * 64},
            lineage={"git_tree_sha": "a" * 40},
            prepared_source_archive=None,
            clock=lambda: pd.Timestamp("2026-09-11T12:00:00Z").to_pydatetime(),
            process_isolated_rebuilds=True,
        )
    artifact_root = paths["artifact_root"]
    assert isinstance(artifact_root, Path)
    assert list(artifact_root.glob(".qlib-v3-freeze-*")) == []
    assert not (artifact_root / "epochs").exists()


def test_transaction_cleanup_removes_staging_after_checkpoint_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )

    _, paths = _transaction_fixture(
        monkeypatch,
        tmp_path,
        checkpoint_error=v3.FreezeV3Error("injected_checkpoint_failure"),
    )
    with pytest.raises(v3.FreezeV3Error, match="injected_checkpoint_failure"):
        v3._materialize_with_trainer(
            project_root=paths["project_root"],
            outcome_source=paths["outcome_source"],
            market_source=paths["market_source"],
            artifact_root=paths["artifact_root"],
            report_path=paths["report_path"],
            checkpoint_root=paths["checkpoint_root"],
            stress_bps=5.0,
            trainer=lambda *args, **kwargs: None,
            trainer_metadata={"framework": "test"},
            environment_contract={"deterministic_sha256": "9" * 64},
            lineage={"git_tree_sha": "a" * 40},
            prepared_source_archive=None,
            clock=lambda: pd.Timestamp("2026-09-11T12:00:00Z").to_pydatetime(),
            process_isolated_rebuilds=True,
        )
    artifact_root = paths["artifact_root"]
    assert isinstance(artifact_root, Path)
    assert list(artifact_root.glob(".qlib-v3-freeze-*")) == []
    assert not (artifact_root / "epochs").exists()


def test_post_publication_report_failure_preserves_certified_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from smartcrypto.runtime.integrity_traceability_v2 import AtomicWriteError

    result, _, paths = _run_transaction_materialization(
        monkeypatch,
        tmp_path,
        report_error=AtomicWriteError("injected_report_failure"),
    )
    assert result["status"] == "warning"
    assert result["reason"] == "v3_freeze_certified_report_write_failed"
    assert result["decision"] == "V3_REPRODUCIBLE_EPOCH_READY_FOR_ACTIVATION"
    assert result["v3_freeze_reproducible"] is True
    assert result["v3_freeze_dod"] == "READY_FOR_ACTIVATION"
    assert result["report_write_performed"] is False
    assert result["report_write_status"] == "warning"
    assert result["report_write_error_class"] == "AtomicWriteError"
    assert result["write_performed"] is True
    epoch_dir = Path(str(result["epoch_dir"]))
    assert epoch_dir.is_dir()
    artifact_root = paths["artifact_root"]
    assert isinstance(artifact_root, Path)
    assert list(artifact_root.glob(".qlib-v3-freeze-*")) == []


def test_normal_report_write_keeps_success_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result, state, _ = _run_transaction_materialization(monkeypatch, tmp_path)
    assert result["status"] == "ok"
    assert result["report_write_performed"] is True
    assert result["report_write_status"] == "ok"
    assert "report_write_error_class" not in result
    reports = state["reports"]
    assert isinstance(reports, list)
    assert len(reports) == 1
    persisted = reports[0]
    assert isinstance(persisted, dict)
    assert persisted["report_write_performed"] is True
    assert persisted["report_write_status"] == "ok"


def test_retry_after_report_failure_does_not_overwrite_immutable_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from smartcrypto.learning.paper_autolearning import (
        qlib_v3_reproducible_freeze_epoch as v3,
    )
    from smartcrypto.runtime.integrity_traceability_v2 import AtomicWriteError

    result, _, paths = _run_transaction_materialization(
        monkeypatch,
        tmp_path,
        report_error=AtomicWriteError("injected_report_failure"),
    )
    epoch_dir = Path(str(result["epoch_dir"]))
    marker = epoch_dir / "immutable-marker.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(v3.FreezeV3Error, match="immutable_epoch_already_exists"):
        v3._materialize_with_trainer(
            project_root=paths["project_root"],
            outcome_source=paths["outcome_source"],
            market_source=paths["market_source"],
            artifact_root=paths["artifact_root"],
            report_path=paths["report_path"],
            checkpoint_root=paths["checkpoint_root"],
            stress_bps=5.0,
            trainer=lambda *args, **kwargs: None,
            trainer_metadata={"framework": "test"},
            environment_contract={"deterministic_sha256": "9" * 64},
            lineage={"git_tree_sha": "a" * 40},
            prepared_source_archive=None,
            clock=lambda: pd.Timestamp("2026-09-11T12:00:00Z").to_pydatetime(),
            process_isolated_rebuilds=True,
        )
    assert marker.read_text(encoding="utf-8") == "preserve\n"
    artifact_root = paths["artifact_root"]
    assert isinstance(artifact_root, Path)
    assert list(artifact_root.glob(".qlib-v3-freeze-*")) == []
