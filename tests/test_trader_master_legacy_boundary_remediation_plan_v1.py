from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2 import legacy_master_remediation_plan as plan


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan_trader_master_legacy_boundary_remediation_v1.py"
BASE_POLICY = ROOT / "config" / "trader_master_legacy_research_only_policy_v1.json"
BASE_TAXONOMY = ROOT / "config" / "trader_master_legacy_boundary_remediation_taxonomy_v1.json"
FIXED_TIME = "2026-07-13T00:00:00+00:00"
FIXED_COMMIT = "a" * 40


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_project(root: Path) -> tuple[Path, Path, plan.RemediationTaxonomy]:
    policy = root / "config" / BASE_POLICY.name
    taxonomy = root / "config" / BASE_TAXONOMY.name
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_bytes(BASE_POLICY.read_bytes())
    taxonomy.write_bytes(BASE_TAXONOMY.read_bytes())
    loaded = plan.load_remediation_taxonomy(project_root=root, taxonomy_path=taxonomy)
    return policy, taxonomy, loaded


def write_source(root: Path, relative_path: str, *, lines: int = 12) -> Path:
    path = root / relative_path.replace("\\", "/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"value_{index} = {index}" for index in range(lines)), encoding="utf-8")
    return path


def finding(
    classification: str,
    *,
    severity: str = "high",
    relative_path: str = "smartcrypto/research/consumer.py",
    line_number: int = 2,
    symbol: str = "MASTER",
    evidence: str = "literal_master_reference",
    finding_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "classification": classification,
        "severity": severity,
        "relative_path": relative_path,
        "line_number": line_number,
        "symbol": symbol,
        "evidence": evidence,
    }
    payload["finding_id"] = finding_id or hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:24]
    return payload


def boundary(*findings: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "trader_master_legacy_research_only_boundary_report_v1",
        "status": "ok",
        "reason": "legacy_master_boundary_violations_detected",
        "decision": "LEGACY_MASTER_BOUNDARY_VIOLATED",
        "high_count": sum(item["severity"] == "high" for item in findings),
        "critical_count": sum(item["severity"] == "critical" for item in findings),
        "dynamic_reference_unresolved_count": sum(
            item["classification"] == "dynamic_reference_unresolved" for item in findings
        ),
        "consumer_inventory_complete": not any(
            item["classification"] == "dynamic_reference_unresolved" for item in findings
        ),
        "segregation_enforced": False,
        "findings": list(findings),
    }
    payload.update(overrides)
    return payload


def build(root: Path, *findings: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    policy, taxonomy, _ = setup_project(root)
    for payload in findings:
        relative = str(payload["relative_path"])
        if not relative.startswith(".."):
            write_source(root, relative, lines=max(12, int(payload.get("line_number", 0)) + 1))
    return plan.build_remediation_plan_report(
        project_root=root,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=boundary(*findings),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
        generated_at_utc=FIXED_TIME,
        **kwargs,
    )


def action_for(
    taxonomy: plan.RemediationTaxonomy,
    payload: dict[str, Any],
    *,
    proof: plan.StructuralProof | None = None,
) -> plan.RemediationAction:
    reference = plan.finding_reference_from_payload(payload)
    return plan.classify_finding(reference, taxonomy, structural_proof=proof).recommended_action


def test_valid_taxonomy_loads(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    assert plan.verify_remediation_taxonomy(taxonomy) == ()
    assert set(taxonomy.closed_actions) == set(plan.RemediationAction)


def test_unknown_action_blocks(tmp_path: Path) -> None:
    policy, taxonomy_path, _ = setup_project(tmp_path)
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    payload["closed_actions"][0] = "UNKNOWN_ACTION"
    taxonomy_path.write_text(json.dumps(payload), encoding="utf-8")
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy_path,
        source_boundary_report=boundary(),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "remediation_taxonomy_unreadable"


def test_incomplete_precedence_blocks(tmp_path: Path) -> None:
    policy, taxonomy_path, _ = setup_project(tmp_path)
    payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    payload["action_precedence"].pop()
    taxonomy_path.write_text(json.dumps(payload), encoding="utf-8")
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy_path,
        source_boundary_report=boundary(),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["status"] == "blocked"
    assert "taxonomy_action_precedence_incomplete" in report["validation_errors"]


def test_invalid_source_policy_blocks(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["write_authorized"] = True
    policy.write_text(json.dumps(payload), encoding="utf-8")
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=boundary(),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "source_policy_invalid"


def test_blocked_source_audit_is_fail_closed(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=boundary(status="blocked", reason="git_unavailable"),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["status"] == "blocked"
    assert report["decision"] == plan.RemediationDecision.SOURCE_AUDIT_BLOCKED.value


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_high_and_critical_findings_are_accounted(tmp_path: Path, severity: str) -> None:
    payload = finding("unregistered_master_consumer", severity=severity)
    report = build(tmp_path, payload)
    assert report["relevant_finding_count"] == 1
    assert report["classified_finding_count"] == 1


@pytest.mark.parametrize(
    "classification",
    ["legacy_writer_callsite", "direct_master_write", "direct_master_import"],
)
def test_writer_findings_map_to_removal(
    tmp_path: Path,
    classification: str,
) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    assert action_for(taxonomy, finding(classification, severity="critical")) == (
        plan.RemediationAction.REMOVE_LEGACY_WRITER_CALLSITE
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "smartcrypto/execution/a.py",
        "smartcrypto/risk/a.py",
        "smartcrypto/runtime/a.py",
        "smartcrypto/learning/a.py",
        "smartcrypto/qlib_engine/a.py",
        "scripts/paper_selection.py",
    ],
)
def test_operational_references_map_to_isolation(
    tmp_path: Path,
    relative_path: str,
) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding(
        "prohibited_operational_consumer",
        severity="critical",
        relative_path=relative_path,
    )
    assert action_for(taxonomy, payload) == plan.RemediationAction.ISOLATE_OPERATIONAL_REFERENCE


def test_research_direct_read_maps_to_adapter(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding("unregistered_master_consumer")
    assert action_for(taxonomy, payload) == (
        plan.RemediationAction.REFACTOR_TO_INSTITUTIONAL_READONLY_ADAPTER
    )


def test_institutional_readonly_consumer_maps_to_registration(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding(
        "unregistered_master_consumer",
        symbol="read_trader_master_readonly",
        evidence="institutional_readonly_adapter",
    )
    reference = plan.finding_reference_from_payload(payload)
    item = plan.classify_finding(reference, taxonomy)
    assert item.recommended_action == plan.RemediationAction.REGISTER_AS_READONLY_RESEARCH_CONSUMER
    assert item.future_policy_change_required is True


def test_writer_implementation_remains_quarantined(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding(
        "legacy_writer_implementation",
        severity="info",
        relative_path="smartcrypto/data/trades_importer.py",
        line_number=0,
    )
    item = plan.classify_finding(plan.finding_reference_from_payload(payload), taxonomy)
    assert item.recommended_action == plan.RemediationAction.KEEP_QUARANTINED_LEGACY_IMPLEMENTATION
    assert item.safety_flags == tuple(sorted(plan.PLAN_SAFETY_FLAGS.items()))


def test_dynamic_reference_maps_to_resolution(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding("dynamic_reference_unresolved", severity="medium")
    assert action_for(taxonomy, payload) == plan.RemediationAction.RESOLVE_DYNAMIC_REFERENCE


def test_dynamic_operational_reference_is_not_downgraded(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding(
        "dynamic_reference_unresolved",
        severity="medium",
        relative_path="smartcrypto/learning/dynamic.py",
    )
    assert action_for(taxonomy, payload) == plan.RemediationAction.ISOLATE_OPERATIONAL_REFERENCE


def test_false_positive_requires_structural_proof(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding("test_fixture_reference", severity="high", relative_path="tests/fixture.py")
    proof = plan.StructuralProof(
        structural_proof="isolated_synthetic_literal",
        proof_method="source_auditor_test_fixture_classification",
        proof_reproducible=True,
    )
    assert action_for(taxonomy, payload, proof=proof) == (
        plan.RemediationAction.FALSE_POSITIVE_WITH_STRUCTURAL_PROOF
    )


def test_filename_alone_is_not_false_positive_proof(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding("test_fixture_reference", severity="high", relative_path="tests/fixture.py")
    assert action_for(taxonomy, payload) == plan.RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW


def test_ambiguous_finding_maps_to_manual_review(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    payload = finding("unknown_high_finding")
    assert action_for(taxonomy, payload) == plan.RemediationAction.BLOCKED_REQUIRES_MANUAL_REVIEW


def test_each_finding_has_exactly_one_action(tmp_path: Path) -> None:
    payloads = [
        finding("direct_master_import", relative_path="scripts/importer.py"),
        finding("dynamic_reference_unresolved", severity="medium", relative_path="research.py"),
    ]
    report = build(tmp_path, *payloads)
    assert report["classified_finding_count"] == 2
    assert all(isinstance(item["recommended_action"], str) for item in report["remediation_items"])
    assert report["multiply_classified_finding_count"] == 0


def test_duplicate_id_with_same_payload_is_deduplicated(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer", finding_id="same")
    report = build(tmp_path, payload, dict(payload))
    assert report["source_finding_count"] == 2
    assert report["relevant_finding_count"] == 1
    assert report["remediation_item_count"] == 1


def test_duplicate_id_with_divergent_payload_is_incomplete(tmp_path: Path) -> None:
    first = finding("unregistered_master_consumer", finding_id="same")
    second = finding(
        "direct_master_import",
        severity="critical",
        relative_path="scripts/importer.py",
        finding_id="same",
    )
    report = build(tmp_path, first, second)
    assert report["decision"] == plan.RemediationDecision.PLAN_INCOMPLETE.value
    assert any("duplicate_source_finding_id_conflict" in item for item in report["validation_errors"])


def test_inconsistent_accounting_is_plan_incomplete(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer", finding_id="same")
    conflicting = dict(payload, symbol="different")
    report = build(tmp_path, payload, conflicting)
    assert report["plan_accounting_consistent"] is False
    assert report["unclassified_finding_count"] == 1
    assert report["decision"] == plan.RemediationDecision.PLAN_INCOMPLETE.value


@pytest.mark.parametrize(
    ("classification", "relative_path", "expected_branch"),
    [
        ("direct_master_import", "scripts/importer.py", "writer_callsite_removal"),
        ("prohibited_operational_consumer", "smartcrypto/execution/a.py", "operational_reference_isolation"),
        ("dynamic_reference_unresolved", "research.py", "dynamic_reference_resolution"),
        ("unregistered_master_consumer", "smartcrypto/research/a.py", "readonly_adapter_migration"),
    ],
)
def test_actions_are_assigned_to_canonical_branches(
    tmp_path: Path,
    classification: str,
    relative_path: str,
    expected_branch: str,
) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    severity = "medium" if classification == "dynamic_reference_unresolved" else "critical"
    item = plan.classify_finding(
        plan.finding_reference_from_payload(
            finding(classification, severity=severity, relative_path=relative_path)
        ),
        taxonomy,
    )
    packages, _, errors = plan.build_branch_packages(items=[item], taxonomy=taxonomy)
    assert errors == ()
    package = next(value for value in packages if value.branch_id == expected_branch)
    assert item.finding_id in package.finding_ids


def test_registration_is_assigned_to_branch_five(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    item = plan.classify_finding(
        plan.finding_reference_from_payload(
            finding(
                "unregistered_master_consumer",
                symbol="read_trader_master_readonly",
                evidence="institutional_readonly_adapter",
            )
        ),
        taxonomy,
    )
    packages, _, _ = plan.build_branch_packages(items=[item], taxonomy=taxonomy)
    package = next(value for value in packages if value.branch_id == "readonly_consumer_registration")
    assert package.policy_update_allowed is True
    assert plan.POLICY_RELATIVE_PATH in package.target_files


def test_quarantine_item_is_assigned_to_closeout(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    item = plan.classify_finding(
        plan.finding_reference_from_payload(
            finding(
                "legacy_writer_implementation",
                severity="info",
                relative_path="smartcrypto/data/trades_importer.py",
                line_number=0,
            )
        ),
        taxonomy,
    )
    packages, _, _ = plan.build_branch_packages(items=[item], taxonomy=taxonomy)
    assert [value.branch_id for value in packages] == ["reverification_closeout"]


def test_dependencies_follow_canonical_order(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer")
    report = build(tmp_path, payload)
    packages = {item["branch_id"]: item for item in report["branch_packages"]}
    assert packages["operational_reference_isolation"]["dependencies"] == ["writer_callsite_removal"]
    assert packages["dynamic_reference_resolution"]["dependencies"] == [
        "writer_callsite_removal",
        "operational_reference_isolation",
    ]
    assert packages["readonly_adapter_migration"]["dependencies"] == [
        "writer_callsite_removal",
        "operational_reference_isolation",
        "dynamic_reference_resolution",
    ]


def test_dependency_cycle_is_detected(tmp_path: Path) -> None:
    _, _, taxonomy = setup_project(tmp_path)
    templates = list(taxonomy.future_branch_templates)
    templates[0] = replace(templates[0], dependencies=("operational_reference_isolation",))
    cyclic = replace(taxonomy, future_branch_templates=tuple(templates))
    item = plan.classify_finding(
        plan.finding_reference_from_payload(
            finding(
                "prohibited_operational_consumer",
                relative_path="smartcrypto/execution/a.py",
            )
        ),
        cyclic,
    )
    _, _, errors = plan.build_branch_packages(items=[item], taxonomy=cyclic)
    assert "branch_dependency_cycle_detected" in errors


def test_target_paths_are_normalized_inside_project(tmp_path: Path) -> None:
    payload = finding(
        "unregistered_master_consumer",
        relative_path="smartcrypto\\research\\consumer.py",
    )
    report = build(tmp_path, payload)
    targets = {
        target for package in report["branch_packages"] for target in package["target_files"]
    }
    assert "smartcrypto/research/consumer.py" in targets


def test_external_finding_path_blocks(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer", relative_path="../outside.py")
    report = build(tmp_path, payload)
    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_finding_path"


def test_symlink_finding_path_blocks(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    target = write_source(tmp_path, "target.py")
    link = tmp_path / "consumer.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    payload = finding("unregistered_master_consumer", relative_path="consumer.py")
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=boundary(payload),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "unsafe_finding_path"


def test_packages_never_target_data_trades_or_fingerprint(tmp_path: Path) -> None:
    report = build(tmp_path, finding("unregistered_master_consumer"))
    targets = {
        target for package in report["branch_packages"] for target in package["target_files"]
    }
    assert not any(target.startswith("data/trades/") for target in targets)
    assert plan.FINGERPRINT_SPEC_PATH not in targets


@pytest.mark.parametrize(
    "flag",
    [
        "bridge_authorized",
        "import_authorized",
        "write_authorized",
        "operational_training_authorized",
    ],
)
def test_plan_never_authorizes_prohibited_capabilities(tmp_path: Path, flag: str) -> None:
    report = build(tmp_path, finding("unregistered_master_consumer"))
    assert report[flag] is False
    assert all(item["safety_flags"][flag] is False for item in report["remediation_items"])


def test_policy_is_not_changed(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    before = sha256(policy)
    plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=boundary(),
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert sha256(policy) == before


def test_consumers_are_not_changed(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer")
    source = write_source(tmp_path, payload["relative_path"])
    before = sha256(source)
    build(tmp_path, payload)
    assert sha256(source) == before


def test_trades_importer_is_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    writer = tmp_path / plan.LEGACY_WRITER_PATH
    writer.parent.mkdir(parents=True, exist_ok=True)
    writer.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    payload = finding(
        "legacy_writer_implementation",
        severity="info",
        relative_path=plan.LEGACY_WRITER_PATH,
        line_number=0,
    )
    build(tmp_path, payload)
    assert not marker.exists()


def test_source_auditor_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    called: dict[str, Any] = {}

    def fake_audit(**kwargs: Any) -> dict[str, Any]:
        called.update(kwargs)
        return boundary(
            decision="LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY",
            high_count=0,
            critical_count=0,
            dynamic_reference_unresolved_count=0,
            consumer_inventory_complete=True,
            segregation_enforced=True,
        )

    monkeypatch.setattr(plan, "build_legacy_master_boundary_report", fake_audit)
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert called["write_report"] is False
    assert report["decision"] == plan.RemediationDecision.NOT_REQUIRED.value


def test_ast_scanner_and_parquet_reader_are_not_duplicated() -> None:
    source = inspect.getsource(plan)
    assert "import ast" not in source
    assert "git ls-files" not in source
    assert "read_parquet(" not in source
    assert "read_trader_master_readonly(" not in source


def test_default_does_not_write(tmp_path: Path) -> None:
    report = build(tmp_path, finding("unregistered_master_consumer"))
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()


def test_write_report_is_limited_to_data_reports(tmp_path: Path) -> None:
    report = build(
        tmp_path,
        finding("unregistered_master_consumer"),
        write_report=True,
    )
    assert report["write_performed"] is True
    assert (tmp_path / plan.DEFAULT_JSON_REPORT).is_file()
    assert (tmp_path / plan.DEFAULT_MARKDOWN_REPORT).is_file()


def test_write_report_rejects_external_output(tmp_path: Path) -> None:
    report = build(
        tmp_path,
        finding("unregistered_master_consumer"),
        write_report=True,
        output_json="outside.json",
        output_markdown="outside.md",
    )
    assert report["status"] == "blocked"
    assert report["write_performed"] is False


def test_json_and_markdown_preserve_prohibitive_flags(tmp_path: Path) -> None:
    report = build(
        tmp_path,
        finding("unregistered_master_consumer"),
        write_report=True,
    )
    payload = json.loads((tmp_path / plan.DEFAULT_JSON_REPORT).read_text(encoding="utf-8"))
    markdown = (tmp_path / plan.DEFAULT_MARKDOWN_REPORT).read_text(encoding="utf-8")
    for field, expected in plan.PLAN_SAFETY_FLAGS.items():
        assert report[field] is expected
        assert payload[field] is expected
    assert "Remediation applied: `false`" in markdown


def test_output_is_deterministic_except_generated_at(tmp_path: Path) -> None:
    first = build(tmp_path, finding("unregistered_master_consumer"))
    second = build(tmp_path, finding("unregistered_master_consumer"))
    first.pop("generated_at_utc")
    second.pop("generated_at_utc")
    assert first == second


def test_clean_source_audit_returns_not_required(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    clean = boundary(
        decision="LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY",
        reason="legacy_master_boundary_compliant",
        high_count=0,
        critical_count=0,
        dynamic_reference_unresolved_count=0,
        consumer_inventory_complete=True,
        segregation_enforced=True,
    )
    report = plan.build_remediation_plan_report(
        project_root=tmp_path,
        policy_path=policy,
        taxonomy_path=taxonomy,
        source_boundary_report=clean,
        source_commit_sha=FIXED_COMMIT,
        source_branch="fixture",
    )
    assert report["decision"] == plan.RemediationDecision.NOT_REQUIRED.value


def test_all_classified_without_manual_review_is_ready(tmp_path: Path) -> None:
    report = build(tmp_path, finding("unregistered_master_consumer"))
    assert report["manual_review_count"] == 0
    assert report["decision"] == plan.RemediationDecision.PLAN_READY.value


def test_manual_review_present_changes_decision(tmp_path: Path) -> None:
    report = build(tmp_path, finding("unknown_high_finding"))
    assert report["manual_review_count"] == 1
    assert report["decision"] == plan.RemediationDecision.REQUIRES_MANUAL_REVIEW.value


def test_unclassified_finding_changes_decision_to_incomplete(tmp_path: Path) -> None:
    payload = finding("unregistered_master_consumer", finding_id="same")
    report = build(tmp_path, payload, dict(payload, relative_path="other.py"))
    assert report["unclassified_finding_count"] == 1
    assert report["decision"] == plan.RemediationDecision.PLAN_INCOMPLETE.value


def test_cli_executes_without_pythonpath(tmp_path: Path) -> None:
    policy, taxonomy, _ = setup_project(tmp_path)
    master = tmp_path / "data" / "trades" / "trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([{"symbol": "BTCUSDT", "side": "long"}])
    frame.to_parquet(master, index=False)
    policy_payload = json.loads(policy.read_text(encoding="utf-8"))
    policy_payload.update(
        expected_sha256=sha256(master),
        expected_size_bytes=master.stat().st_size,
        expected_row_count=1,
        expected_schema_columns=list(frame.columns),
    )
    policy.write_text(json.dumps(policy_payload), encoding="utf-8")
    (tmp_path / "README.md").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    completed = subprocess.run(  # nosec B603 - fixed interpreter and local script
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--policy",
            str(policy),
            "--taxonomy",
            str(taxonomy),
            "--trader-master",
            str(master),
            "--no-write",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["write_performed"] is False
    assert payload["operational_authority"] is False


def test_real_data_artifact_is_not_changed_by_injected_plan(tmp_path: Path) -> None:
    master = ROOT / "data" / "trades" / "trades_master.parquet"
    before = sha256(master)
    build(tmp_path, finding("unregistered_master_consumer"))
    assert sha256(master) == before
