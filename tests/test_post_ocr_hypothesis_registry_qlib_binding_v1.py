from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from smartcrypto.learning.qlib_trainer import ranking_trainer
from smartcrypto.research.trades_master_official.hypothesis_registry import (
    EXPECTED_QLIB_HYPOTHESIS_ID,
    EXPECTED_PORTABLE_REGISTRY_HASH,
    EXPECTED_WQ6_DATASET_HASH,
    EXPECTED_WQ6_SPLIT_MANIFEST_HASH,
    OfficialHypothesisRegistryError,
)


def fake_bundle(
    *,
    dataset_hash: str = EXPECTED_WQ6_DATASET_HASH,
    split_hash: str = EXPECTED_WQ6_SPLIT_MANIFEST_HASH,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_manifest={
            "dataset_hash": dataset_hash,
        },
        walkforward={
            "split_manifest_hash": split_hash,
            "split_count": 3,
        },
    )


def fake_h01() -> dict[str, Any]:
    return {
        "hypothesis_id": (
            EXPECTED_QLIB_HYPOTHESIS_ID
        ),
        "candidate_class": (
            "qlib_ranking_challenger"
        ),
        "state": "HOLD",
        "dataset_fingerprint": {
            "dataset_hash": (
                EXPECTED_WQ6_DATASET_HASH
            ),
            "dataset_file_sha256": (
                "70ccb7f88aa281fc90ebbc77920b89340"
                "b688477ec29fbe6a373013d15213973"
            ),
        },
        "split_fingerprint": {
            "split_manifest_hash": (
                EXPECTED_WQ6_SPLIT_MANIFEST_HASH
            ),
        },
        "authority": {
            "operational_authority": False,
            "sends_orders": False,
            "changes_risk": False,
            "can_apply_to_freqtrade": False,
            "can_apply_to_risk_manager": False,
            "exchange_private_access": False,
        },
    }


def install_valid_registry_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ranking_trainer,
        "load_post_ocr_hypothesis_registry",
        lambda _path: {
            "registry_hash": (
                EXPECTED_PORTABLE_REGISTRY_HASH
            ),
        },
    )

    monkeypatch.setattr(
        ranking_trainer,
        "get_registered_hypothesis",
        lambda _registry, _hypothesis_id: (
            fake_h01()
        ),
    )


def test_legacy_invocation_requires_no_binding() -> None:
    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=None,
            hypothesis_id=None,
            bundle=fake_bundle(),
            train=False,
        )
    )

    assert errors == []

    assert (
        binding[
            "binding_requested"
        ]
        is False
    )

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )

    assert (
        binding[
            "binding_status"
        ]
        == "not_requested"
    )


@pytest.mark.parametrize(
    (
        "registry_path",
        "hypothesis_id",
    ),
    [
        (
            "registry.json",
            None,
        ),
        (
            None,
            EXPECTED_QLIB_HYPOTHESIS_ID,
        ),
    ],
)
def test_incomplete_binding_fails_closed(
    registry_path: str | None,
    hypothesis_id: str | None,
) -> None:
    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                registry_path
            ),
            hypothesis_id=(
                hypothesis_id
            ),
            bundle=fake_bundle(),
            train=False,
        )
    )

    assert (
        "wq7_binding_parameters_incomplete"
        in errors
    )

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )


def test_valid_h01_dry_run_consumes_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_valid_registry_mocks(
        monkeypatch
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(),
            train=False,
        )
    )

    assert errors == []

    assert (
        binding[
            "consumed_registry"
        ]
        is True
    )

    assert (
        binding[
            "binding_status"
        ]
        == "consumed_hold_dry_run"
    )

    assert (
        binding[
            "registry_hash"
        ]
        == EXPECTED_PORTABLE_REGISTRY_HASH
    )

    assert (
        binding[
            "hypothesis_state"
        ]
        == "HOLD"
    )

    assert (
        binding[
            "dataset_fingerprint_match"
        ]
        is True
    )

    assert (
        binding[
            "split_fingerprint_match"
        ]
        is True
    )

    assert (
        binding[
            "training_authorized"
        ]
        is False
    )


def test_h01_hold_blocks_training(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_valid_registry_mocks(
        monkeypatch
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(),
            train=True,
        )
    )

    assert errors == [
        "wq7_hypothesis_state_hold_blocks_training"
    ]

    assert (
        binding[
            "consumed_registry"
        ]
        is True
    )

    assert (
        binding[
            "binding_status"
        ]
        == "consumed_hold_training_blocked"
    )

    assert (
        binding[
            "training_authorized"
        ]
        is False
    )


def test_dataset_fingerprint_drift_blocks_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_valid_registry_mocks(
        monkeypatch
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(
                dataset_hash=(
                    "drift"
                ),
            ),
            train=False,
        )
    )

    assert (
        "wq7_dataset_fingerprint_mismatch"
        in errors
    )

    assert (
        binding[
            "dataset_fingerprint_match"
        ]
        is False
    )

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )


def test_split_fingerprint_drift_blocks_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_valid_registry_mocks(
        monkeypatch
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(
                split_hash="drift",
            ),
            train=False,
        )
    )

    assert (
        "wq7_split_fingerprint_mismatch"
        in errors
    )

    assert (
        binding[
            "split_fingerprint_match"
        ]
        is False
    )

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )


def test_invalid_registry_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_registry(
        _path: str,
    ) -> dict[str, Any]:
        raise (
            OfficialHypothesisRegistryError(
                "registry_validation_failed"
            )
        )

    monkeypatch.setattr(
        ranking_trainer,
        "load_post_ocr_hypothesis_registry",
        fail_registry,
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(),
            train=False,
        )
    )

    assert errors == [
        "wq7_registry_invalid:"
        "registry_validation_failed"
    ]

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )


def test_wrong_hypothesis_class_blocks_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_valid_registry_mocks(
        monkeypatch
    )

    wrong = fake_h01()

    wrong[
        "candidate_class"
    ] = "segment_filter"

    monkeypatch.setattr(
        ranking_trainer,
        "get_registered_hypothesis",
        lambda _registry, _hypothesis_id: (
            wrong
        ),
    )

    binding, errors = (
        ranking_trainer.resolve_wq7_hypothesis_binding(
            hypothesis_registry_path=(
                "registry.json"
            ),
            hypothesis_id=(
                EXPECTED_QLIB_HYPOTHESIS_ID
            ),
            bundle=fake_bundle(),
            train=False,
        )
    )

    assert (
        "wq7_hypothesis_class_mismatch"
        in errors
    )

    assert (
        binding[
            "consumed_registry"
        ]
        is False
    )


def test_cli_exposes_wq7_binding_parameters() -> None:
    parser_source = (
        (
            ranking_trainer.Path(
                "scripts/"
                "train_qlib_institutional_ranking_challenger_v1.py"
            )
        )
        .read_text(
            encoding="utf-8"
        )
    )

    assert (
        "--hypothesis-registry"
        in parser_source
    )

    assert (
        "--hypothesis-id"
        in parser_source
    )
