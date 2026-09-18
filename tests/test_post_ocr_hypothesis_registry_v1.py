from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from smartcrypto.research.trades_master_official.hypothesis_registry import (
    EXPECTED_PORTABLE_REGISTRY_HASH,
    OfficialHypothesisRegistryError,
    canonical_registry_payload,
    load_candidate_queue_v2,
    load_method_freeze,
    registry_content_sha256,
    validate_candidate_queue_v2,
    validate_post_ocr_hypothesis_registry,
)


ROOT = Path(__file__).resolve().parents[1]

MODULE = (
    ROOT
    / "smartcrypto"
    / "research"
    / "trades_master_official"
    / "hypothesis_registry.py"
)

CLI = (
    ROOT
    / "scripts"
    / "build_post_ocr_hypothesis_registry_v1.py"
)

DOC = (
    ROOT
    / "docs"
    / "POST_OCR_HYPOTHESIS_REGISTRY_V1.md"
)


def test_versioned_files_exist() -> None:
    assert MODULE.is_file()
    assert CLI.is_file()
    assert DOC.is_file()


def test_registry_hash_is_path_portable() -> None:
    payload_a = {
        "schema_version": "synthetic",
        "method_freeze": {
            "method_hash": "a" * 64,
            "file_sha256": "b" * 64,
            "path": (
                r"E:\machine-a\freeze.json"
            ),
        },
        "source_queue": {
            "queue_hash": "c" * 64,
            "file_sha256": "d" * 64,
            "path": (
                r"E:\machine-a\queue.json"
            ),
        },
        "economic_payload": {
            "expected_net_pnl": 1.25,
        },
    }

    payload_b = copy.deepcopy(
        payload_a
    )

    payload_b[
        "method_freeze"
    ][
        "path"
    ] = (
        "/opt/machine-b/freeze.json"
    )

    payload_b[
        "source_queue"
    ][
        "path"
    ] = (
        "/opt/machine-b/queue.json"
    )

    assert (
        registry_content_sha256(
            payload_a
        )
        == registry_content_sha256(
            payload_b
        )
    )

    canonical_a = (
        canonical_registry_payload(
            payload_a
        )
    )

    canonical_b = (
        canonical_registry_payload(
            payload_b
        )
    )

    assert canonical_a == canonical_b

    assert (
        canonical_a[
            "method_freeze"
        ][
            "path"
        ]
        is None
    )

    assert (
        canonical_a[
            "source_queue"
        ][
            "path"
        ]
        is None
    )


def test_registry_hash_changes_for_semantic_content() -> None:
    payload = {
        "method_freeze": {
            "path": "A",
            "method_hash": "a" * 64,
        },
        "source_queue": {
            "path": "B",
            "queue_hash": "b" * 64,
        },
        "economic_payload": {
            "value": 1.0,
        },
    }

    mutated = copy.deepcopy(
        payload
    )

    mutated[
        "economic_payload"
    ][
        "value"
    ] = 2.0

    assert (
        registry_content_sha256(
            payload
        )
        != registry_content_sha256(
            mutated
        )
    )


def test_expected_portable_hash_is_sha256() -> None:
    assert len(
        EXPECTED_PORTABLE_REGISTRY_HASH
    ) == 64

    int(
        EXPECTED_PORTABLE_REGISTRY_HASH,
        16,
    )


def test_invalid_registry_fails_validation() -> None:
    payload = {
        "schema_version": (
            "post_ocr_hypothesis_registry_v1"
        ),
        "registry_version": "V1",
        "status": "ok",
        "engineering_status": "PASS",
        "wq7_status": "REGISTRY_READY",
        "quant_edge_status": "NOT_EVALUATED",
        "registry_hash": "0" * 64,
        "source_evidence": {},
        "hypotheses": [],
        "state_counts": {},
        "candidate_class_counts": {},
        "empty_slots": [],
        "evaluation": {
            "training_performed": False,
        },
        "authority": {},
    }

    errors = (
        validate_post_ocr_hypothesis_registry(
            payload
        )
    )

    assert errors
    assert (
        "registry_expected_hash_mismatch"
        in errors
    )
    assert (
        "registry_hypothesis_count_mismatch"
        in errors
    )
    assert (
        "registry_authority_mismatch"
        in errors
    )


def test_invalid_queue_fails_closed() -> None:
    with pytest.raises(
        OfficialHypothesisRegistryError,
        match="queue_schema_mismatch",
    ):
        validate_candidate_queue_v2(
            {
                "schema_version": "wrong",
            }
        )


def test_load_method_freeze_rejects_wrong_file_sha(
    tmp_path: Path,
) -> None:
    path = (
        tmp_path
        / "freeze.json"
    )

    path.write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        OfficialHypothesisRegistryError,
        match=(
            "method_freeze_file_sha_mismatch"
        ),
    ):
        load_method_freeze(
            path
        )


def test_load_queue_v2_rejects_wrong_file_sha(
    tmp_path: Path,
) -> None:
    path = (
        tmp_path
        / "queue.json"
    )

    path.write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        OfficialHypothesisRegistryError,
        match=(
            "queue_v2_file_sha_mismatch"
        ),
    ):
        load_candidate_queue_v2(
            path
        )


def test_cli_help() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--help",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--method-freeze" in completed.stdout
    assert "--queue-v2" in completed.stdout
    assert "--write" in completed.stdout


def test_cli_missing_inputs_blocks_without_side_effects(
    tmp_path: Path,
) -> None:
    method = (
        tmp_path
        / "missing-method.json"
    )

    queue = (
        tmp_path
        / "missing-queue.json"
    )

    output = (
        tmp_path
        / "registry.json"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--method-freeze",
            str(method),
            "--queue-v2",
            str(queue),
            "--output",
            str(output),
            "--write",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2

    payload = json.loads(
        completed.stdout
    )

    assert payload[
        "status"
    ] == "blocked"

    assert payload[
        "operational_authority"
    ] is False

    assert payload[
        "sends_orders"
    ] is False

    assert payload[
        "changes_risk"
    ] is False

    assert payload[
        "write_performed"
    ] is False

    assert not output.exists()


def test_cli_has_no_operational_exchange_contract() -> None:
    source = CLI.read_text(
        encoding="utf-8"
    )

    forbidden = (
        "ccxt.",
        "create_order(",
        "place_order(",
        "submit_order(",
        "ORDER_SUBMISSION_ENABLED=true",
        "LIVE_ENABLED=true",
    )

    for token in forbidden:
        assert token not in source
