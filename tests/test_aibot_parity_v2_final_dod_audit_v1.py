from __future__ import annotations

from pathlib import Path

from smartcrypto.research.aibot_parity_v2_closeout import auditor


def _materialize_complete_fixture(root: Path) -> None:
    for required in auditor.WAVE_EVIDENCE.values():
        for relative in required:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# static evidence\n", encoding="utf-8")

    contract = root / auditor.ORCHESTRATOR_CONTRACT
    markers = (
        *auditor.REQUIRED_FALSE_MARKERS,
        *auditor.REQUIRED_TRUE_MARKERS,
    )
    contract.write_text("\n".join(markers) + "\n", encoding="utf-8")


def test_complete_software_dod_passes_but_paper_release_remains_blocked(
    tmp_path: Path,
) -> None:
    _materialize_complete_fixture(tmp_path)

    result = auditor.audit_aibot_parity_v2(tmp_path)

    assert result["aibot_parity_v2_software_dod"] == "PASS"
    assert result["ready_for_paper_candidate_evaluation"] is True
    assert result["paper_treatment_release_allowed"] is False
    assert result["paper_activation_performed"] is False
    assert result["operational_authority"] is False
    assert result["waves"]["W10"]["status"] == "BLOCKED_EXTERNAL"
    assert result["waves"]["W11"]["status"] == "CONDITIONAL_NOT_RUN"
    assert result["waves"]["W14"]["status"] == "PASS"


def test_missing_required_evidence_blocks_software_dod(tmp_path: Path) -> None:
    _materialize_complete_fixture(tmp_path)
    missing = tmp_path / auditor.WAVE_EVIDENCE["W5"][0]
    missing.unlink()

    result = auditor.audit_aibot_parity_v2(tmp_path)

    assert result["aibot_parity_v2_software_dod"] == "BLOCKED"
    assert result["waves"]["W5"]["status"] == "BLOCKED"
    assert str(missing.relative_to(tmp_path)) in result["waves"]["W5"]["missing_evidence"]
    assert result["ready_for_paper_candidate_evaluation"] is False


def test_qlib_filesystem_presence_never_upgrades_external_security_gate(
    tmp_path: Path,
) -> None:
    _materialize_complete_fixture(tmp_path)
    qlib = tmp_path / "smartcrypto/research/qlib/__init__.py"
    qlib.parent.mkdir(parents=True, exist_ok=True)
    qlib.write_text("# qlib exists but is not security evidence\n", encoding="utf-8")

    result = auditor.audit_aibot_parity_v2(tmp_path)

    assert result["waves"]["W10"]["status"] == "BLOCKED_EXTERNAL"
    assert result["qlib_security_gate_bypassed"] is False
    assert result["aibot_parity_v2_software_dod"] == "PASS"


def test_forbidden_operational_authority_marker_blocks_closeout(tmp_path: Path) -> None:
    _materialize_complete_fixture(tmp_path)
    contract = tmp_path / auditor.ORCHESTRATOR_CONTRACT
    with contract.open("a", encoding="utf-8") as handle:
        handle.write('"operational_authority": True\n')

    result = auditor.audit_aibot_parity_v2(tmp_path)

    assert result["safety"]["status"] == "BLOCKED"
    assert result["safety"]["forbidden_authority_markers"]
    assert result["aibot_parity_v2_software_dod"] == "BLOCKED"
    assert result["paper_treatment_release_allowed"] is False


def test_audit_result_is_deterministic_for_same_tree(tmp_path: Path) -> None:
    _materialize_complete_fixture(tmp_path)

    first = auditor.audit_aibot_parity_v2(tmp_path)
    second = auditor.audit_aibot_parity_v2(tmp_path)

    assert first == second
    assert first["audit_sha256"] == second["audit_sha256"]
