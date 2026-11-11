from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import build_economic_phase_final_forward_proof_v1 as b17_cli
from smartcrypto.learning.qlib_v3_prospective.contracts import CANONICAL, digest
from smartcrypto.research.aibot_parity import economic_phase_final_forward_proof as b17
from tests.v2_contract_fixtures import context_for_sources, validate_synthetic


@pytest.fixture
def v2_context(tmp_path, monkeypatch):
    state = {"schema_version": "qlib_v3_prospective_evidence_store_v1",
             "identity": CANONICAL.mapping(), "signals": [], "outcomes": []}
    state["store_sha256"] = digest(state)
    context = context_for_sources(tmp_path, {"qlib_v3_store": state})
    validate_synthetic(monkeypatch, context)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V2 preactivation attempted runtime collection")

    for name in ("collect_runtime", "container_read", "container_json", "container_optional_json", "_git"):
        monkeypatch.setattr(b17.governance, name, forbidden)
    return context


def kwargs(context):
    return {
        "descriptor": context[0], "predecessor_registration": context[1],
        "predecessor_raw_sha256": context[2], "known_fingerprints": context[3],
    }


def test_v2_descriptor_recognized_without_activating_or_certifying(v2_context):
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(v2_context))
    assert result["status"] == "blocked"
    assert result["decision"] == "BLOCKED_FORWARD_EVIDENCE"
    assert result["certification_attempted"] is False
    assert result["formal_activation_utc"] is None
    assert "v2_cohort_unregistered_no_t0" in result["blockers"]
    assert result["source_contract"]["descriptor_sha256"] == v2_context[0]["descriptor_sha256"]
    assert result["evidence_status"] == "NOT_COLLECTED"
    assert all(gate["value"] is None for gate in result["sample_gates"].values())
    assert all(value is None for value in result["causal_population"].values())


def test_existing_entrypoint_explicit_v2_never_falls_back(v2_context, tmp_path):
    result = b17.build_economic_phase_final_forward_proof_v1(
        project_root=tmp_path, runtime_root=tmp_path,
        **{"v2_" + key: value for key, value in kwargs(v2_context).items()},
    )
    assert result["evidence_mode"] == "explicit_v2_offline"
    assert result["certification_attempted"] is False
    assert "v2_cohort_unregistered_no_t0" in result["blockers"]


def test_incomplete_v2_context_is_blocked_not_legacy(v2_context, tmp_path):
    result = b17.build_economic_phase_final_forward_proof_v1(
        project_root=tmp_path, runtime_root=tmp_path, v2_descriptor=v2_context[0]
    )
    assert result["blockers"] == ["v2_explicit_context_incomplete"]
    assert result["causal_population"]["eligible_decisions"] is None


def test_unapproved_t0_and_epoch_cannot_enter_b17(v2_context):
    bad = copy.deepcopy(v2_context)
    bad[0]["proposal"]["successor"]["formal_activation_utc"] = "2020-01-01T00:00:00Z"
    bad[0]["proposal"]["successor"]["epoch_id"] = "paper-b-epoch-3"
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(bad))
    assert result["status"] == "blocked" and result["certification_attempted"] is False
    assert result["formal_activation_utc"] is None


def test_invalid_as_of_is_structured_block(v2_context):
    result = b17.build_economic_phase_final_forward_proof_v2(
        **kwargs(v2_context), as_of_utc="not-a-time"
    )
    assert result["status"] == "blocked"
    assert result["evidence_status"] == "NOT_COLLECTED"


def test_financial_gates_and_legacy_parameters_are_preserved(v2_context):
    assert (b17.MIN_OBSERVATION_DAYS, b17.MIN_RESOLVED_DECISIONS, b17.MIN_SELECTED_TRADES) == (45, 200, 50)
    assert b17.MIN_SCORER_COVERAGE == 0.99
    assert b17.MIN_TREATMENT_PROFIT_FACTOR == 1.10
    assert b17.CANONICAL_ADDITIONAL_EXECUTION_STRESS_BPS == 5.0
    assert (b17.BOOTSTRAP_RESAMPLES, b17.BOOTSTRAP_SEED) == (5000, 42)
    result = b17.build_economic_phase_final_forward_proof_v2(**kwargs(v2_context))
    assert "economic_dod_no_unexplained_gap" in result["economic_gates"]
    assert all(gate["passed"] is False for gate in result["economic_gates"].values())


@pytest.fixture
def cli_builder(monkeypatch: pytest.MonkeyPatch) -> Mock:
    builder = Mock(return_value={"status": "blocked"})
    monkeypatch.setattr(b17_cli, "build_economic_phase_final_forward_proof_v1", builder)
    return builder


@pytest.mark.parametrize("runtime_root", ["", "runtime", None], ids=["empty", "explicit", "missing"])
def test_v1_cli_runtime_root(
    cli_builder: Mock, capsys: pytest.CaptureFixture[str], runtime_root: str | None,
) -> None:
    args = ["--project-root", "project", "--json"]
    if runtime_root is None:
        with pytest.raises(SystemExit) as exc:
            b17_cli.main(args)
        assert exc.value.code == 2
        assert "--runtime-root is required" in capsys.readouterr().err
        cli_builder.assert_not_called()
    else:
        assert b17_cli.main([*args, "--runtime-root", runtime_root]) == 2
        cli_builder.assert_called_once_with(project_root="project", runtime_root=runtime_root)


@pytest.mark.parametrize(
    ("runtime_root", "expected_root"), [(None, "project"), ("", ""), ("runtime", "runtime")],
    ids=["missing", "empty", "explicit"],
)
def test_v2_cli_complete_context(
    cli_builder: Mock, tmp_path: Path, runtime_root: str | None, expected_root: str,
) -> None:
    args = ["--project-root", "project", "--json"]
    if runtime_root is not None:
        args.extend(("--runtime-root", runtime_root))
    inputs = {
        "descriptor": b'{"descriptor": true}',
        "predecessor-registration": b'{ "predecessor": true }\n',
        "fingerprints": b'{"fingerprints": true}',
    }
    for name, raw in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(raw)
        args.extend((f"--v2-{name}", str(path)))

    assert b17_cli.main(args) == 2
    cli_builder.assert_called_once_with(
        project_root="project",
        runtime_root=expected_root,
        v2_descriptor={"descriptor": True},
        v2_predecessor_registration={"predecessor": True},
        v2_predecessor_raw_sha256=hashlib.sha256(inputs["predecessor-registration"]).hexdigest(),
        v2_known_fingerprints={"fingerprints": True},
    )


@pytest.mark.parametrize("missing", ["descriptor", "predecessor-registration", "fingerprints"])
def test_v2_cli_incomplete_context_cannot_start_legacy_runtime(
    cli_builder: Mock, capsys: pytest.CaptureFixture[str], missing: str,
) -> None:
    args = ["--runtime-root", "runtime", "--json"]
    for name in ("descriptor", "predecessor-registration", "fingerprints"):
        if name != missing:
            args.extend((f"--v2-{name}", "missing.json"))
    with pytest.raises(SystemExit) as exc:
        b17_cli.main(args)
    assert exc.value.code == 2
    assert "V2 requires" in capsys.readouterr().err
    cli_builder.assert_not_called()
