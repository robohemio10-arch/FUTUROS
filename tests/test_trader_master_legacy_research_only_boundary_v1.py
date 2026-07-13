from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2 import legacy_master_governance as gov


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_trader_master_legacy_research_only_boundary_v1.py"
BASE_POLICY = ROOT / "config" / "trader_master_legacy_research_only_policy_v1.json"
FIXED_TIME = "2026-07-13T00:00:00+00:00"
MASTER_COLUMNS = json.loads(BASE_POLICY.read_text(encoding="utf-8"))["expected_schema_columns"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_project(root: Path) -> tuple[Path, Path, gov.LegacyMasterPolicy]:
    master = root / "data" / "trades" / "trades_master.parquet"
    master.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([{name: f"value-{index}" for index, name in enumerate(MASTER_COLUMNS)}])
    frame.to_parquet(master, index=False)
    policy_payload = json.loads(BASE_POLICY.read_text(encoding="utf-8"))
    policy_payload.update(
        expected_sha256=sha256(master),
        expected_size_bytes=master.stat().st_size,
        expected_row_count=1,
        expected_schema_columns=list(frame.columns),
    )
    policy = root / "config" / BASE_POLICY.name
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps(policy_payload, indent=2), encoding="utf-8")
    return master, policy, gov.load_legacy_master_policy(project_root=root, policy_path=policy)


def good_evidence(policy: gov.LegacyMasterPolicy) -> gov.DatasetEvidence:
    return gov.DatasetEvidence(
        status="ok",
        reason="legacy_master_artifact_matches_policy",
        trader_master_path=policy.relative_path,
        expected_sha256=policy.expected_sha256,
        observed_sha256_before=policy.expected_sha256,
        observed_sha256_after=policy.expected_sha256,
        hash_preserved=True,
        expected_size_bytes=policy.expected_size_bytes,
        observed_size_before=policy.expected_size_bytes,
        observed_size_after=policy.expected_size_bytes,
        size_preserved=True,
        expected_row_count=policy.expected_row_count,
        observed_row_count=policy.expected_row_count,
        expected_schema_columns=policy.expected_schema_columns,
        observed_schema_columns=policy.expected_schema_columns,
        temp_copy_used=True,
        artifact_contract_matches=True,
        validation_errors=(),
    )


def fake_git(paths: list[str] | tuple[str, ...] = ()) -> Any:
    output = "\0".join(paths) + ("\0" if paths else "")

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args[0], 0, output, "")

    return runner


def finding_for(source: str, path: str = "app.py") -> tuple[gov.AuditFinding, ...]:
    return gov.analyze_python_source(path, source)


def test_valid_policy_loads(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    assert gov.verify_legacy_master_policy(policy) == ()
    assert policy.dataset_classification == "research_only_legacy_non_v2"


def test_incomplete_policy_blocks(tmp_path: Path) -> None:
    _, policy_path, _ = setup_project(tmp_path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    payload.pop("dataset_id")
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        runner=fake_git(),
    )
    assert report["status"] == "blocked"
    assert report["decision"] == "LEGACY_MASTER_POLICY_INVALID"


@pytest.mark.parametrize(
    "flag",
    ["write_authorized", "fingerprint_v2_compatible", "operational_authority"],
)
def test_unsafe_policy_flags_are_invalid(tmp_path: Path, flag: str) -> None:
    _, _, policy = setup_project(tmp_path)
    flags = dict(policy.restricted_flags)
    flags[flag] = True
    errors = gov.verify_legacy_master_policy(
        replace(policy, restricted_flags=tuple(flags.items()))
    )
    assert f"policy_restricted_flag_true:{flag}" in errors


def test_matching_artifact_is_accepted(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    evidence = gov.verify_pinned_legacy_master_artifact(
        project_root=tmp_path,
        policy=policy,
    )
    assert evidence.artifact_contract_matches is True
    assert evidence.status == "ok"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("expected_sha256", "a" * 64, "trader_master_sha256_drift"),
        ("expected_size_bytes", 1, "trader_master_size_drift"),
        ("expected_row_count", 2, "trader_master_row_count_drift"),
        ("expected_schema_columns", ("wrong",), "trader_master_schema_drift"),
    ],
)
def test_artifact_drift_is_detected(
    tmp_path: Path,
    field: str,
    value: Any,
    error: str,
) -> None:
    _, _, policy = setup_project(tmp_path)
    evidence = gov.verify_pinned_legacy_master_artifact(
        project_root=tmp_path,
        policy=replace(policy, **{field: value}),
    )
    assert evidence.artifact_contract_matches is False
    assert error in evidence.validation_errors


def test_symlink_master_is_blocked(tmp_path: Path) -> None:
    master, _, policy = setup_project(tmp_path)
    target = master.with_name("target.parquet")
    master.replace(target)
    try:
        master.symlink_to(target)
    except OSError:
        pytest.skip("symlink unavailable")
    evidence = gov.verify_pinned_legacy_master_artifact(
        project_root=tmp_path,
        policy=policy,
    )
    assert evidence.status == "blocked"
    assert "symlink" in evidence.reason


def test_path_outside_project_is_blocked(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    evidence = gov.verify_pinned_legacy_master_artifact(
        project_root=tmp_path,
        policy=policy,
        trader_master_path=tmp_path.parent / "outside.parquet",
    )
    assert evidence.status == "blocked"
    assert evidence.reason == "trader_master_path_policy_mismatch"


def test_reader_uses_temporary_copy_and_preserves_hash(tmp_path: Path) -> None:
    master, _, policy = setup_project(tmp_path)
    before = sha256(master)
    evidence = gov.verify_pinned_legacy_master_artifact(
        project_root=tmp_path,
        policy=policy,
    )
    assert evidence.temp_copy_used is True
    assert evidence.hash_preserved is True
    assert evidence.observed_sha256_before == evidence.observed_sha256_after == before
    assert sha256(master) == before


def test_registered_readonly_consumer_is_allowed(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    registration = policy.registered_consumers[0]
    result = gov.evaluate_legacy_master_access(
        policy=policy,
        dataset_evidence=good_evidence(policy),
        request=gov.AccessRequest(
            consumer_path=registration.relative_path,
            purpose=registration.allowed_purposes[0],
            access_mode=gov.AccessMode.READ_ONLY,
            requested_capabilities=(registration.allowed_capabilities[0],),
        ),
    )
    assert result.decision == gov.AccessDecision.ALLOW_READONLY_RESEARCH
    assert result.allowed is True
    assert result.operational_authority is False


def test_unregistered_consumer_is_denied(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    result = gov.evaluate_legacy_master_access(
        policy=policy,
        dataset_evidence=good_evidence(policy),
        request=gov.AccessRequest(
            consumer_path="unknown.py",
            purpose="historical_readonly_research",
            access_mode=gov.AccessMode.READ_ONLY,
        ),
    )
    assert result.decision == gov.AccessDecision.DENY_UNREGISTERED_CONSUMER


def test_unregistered_purpose_is_denied(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    result = gov.evaluate_legacy_master_access(
        policy=policy,
        dataset_evidence=good_evidence(policy),
        request=gov.AccessRequest(
            consumer_path=policy.registered_consumers[0].relative_path,
            purpose="unknown",
            access_mode=gov.AccessMode.READ_ONLY,
        ),
    )
    assert result.decision == gov.AccessDecision.DENY_PURPOSE_NOT_ALLOWED


@pytest.mark.parametrize(
    ("mode", "decision"),
    [
        (gov.AccessMode.WRITE, gov.AccessDecision.DENY_WRITE_CAPABILITY),
        (gov.AccessMode.IMPORT, gov.AccessDecision.DENY_IMPORT_USE),
        (
            gov.AccessMode.FINGERPRINT_GENERATION,
            gov.AccessDecision.DENY_FINGERPRINT_V2_USE,
        ),
        (gov.AccessMode.DEDUPLICATION, gov.AccessDecision.DENY_DEDUPLICATION_USE),
        (
            gov.AccessMode.OPERATIONAL_TRAINING,
            gov.AccessDecision.DENY_OPERATIONAL_TRAINING_USE,
        ),
        (gov.AccessMode.PAPER_SIGNAL_SELECTION, gov.AccessDecision.DENY_PAPER_SIGNAL_USE),
        (gov.AccessMode.LIVE_SIGNAL_SELECTION, gov.AccessDecision.DENY_LIVE_SIGNAL_USE),
        (gov.AccessMode.RISK_DECISION, gov.AccessDecision.DENY_RISK_USE),
        (gov.AccessMode.ORDER_EXECUTION, gov.AccessDecision.DENY_ORDER_EXECUTION_USE),
    ],
)
def test_prohibited_access_modes_are_denied(
    tmp_path: Path,
    mode: gov.AccessMode,
    decision: gov.AccessDecision,
) -> None:
    _, _, policy = setup_project(tmp_path)
    registration = policy.registered_consumers[0]
    result = gov.evaluate_legacy_master_access(
        policy=policy,
        dataset_evidence=good_evidence(policy),
        request=gov.AccessRequest(
            consumer_path=registration.relative_path,
            purpose=registration.allowed_purposes[0],
            access_mode=mode,
        ),
    )
    assert result.decision == decision
    assert result.allowed is False


@pytest.mark.parametrize(
    "source",
    [
        'MASTER = "data/trades/trades_master.parquet"',
        'MASTER = Path("data/trades/trades_master.parquet")',
    ],
)
def test_ast_detects_literal_and_path(source: str) -> None:
    assert finding_for(source)


def test_ast_detects_import_alias_and_call() -> None:
    findings = finding_for(
        "from smartcrypto.data.trades_importer import write_master as save\n"
        "save(frame)\n"
    )
    assert any(item.classification == gov.FindingClassification.LEGACY_WRITER_CALLSITE for item in findings)


@pytest.mark.parametrize(
    ("source", "classification"),
    [
        ("write_master(frame)", gov.FindingClassification.LEGACY_WRITER_CALLSITE),
        (
            "import_trades_incrementally(frame)",
            gov.FindingClassification.DIRECT_MASTER_IMPORT,
        ),
        (
            'pd.read_parquet("data/trades/trades_master.parquet")',
            gov.FindingClassification.UNREGISTERED_MASTER_CONSUMER,
        ),
        (
            'frame.to_parquet("data/trades/trades_master.parquet")',
            gov.FindingClassification.DIRECT_MASTER_WRITE,
        ),
    ],
)
def test_ast_detects_reader_writer_and_import_calls(
    source: str,
    classification: gov.FindingClassification,
) -> None:
    findings = finding_for(source)
    assert any(item.classification == classification for item in findings)


def test_operational_reference_is_critical() -> None:
    findings = finding_for(
        'MASTER = "data/trades/trades_master.parquet"',
        "smartcrypto/runtime/consumer.py",
    )
    assert any(
        item.classification == gov.FindingClassification.PROHIBITED_OPERATIONAL_CONSUMER
        and item.severity == gov.Severity.CRITICAL
        for item in findings
    )


def test_unregistered_consumer_is_high() -> None:
    findings = finding_for('MASTER = "data/trades/trades_master.parquet"')
    assert any(item.severity == gov.Severity.HIGH for item in findings)


def test_fingerprint_v2_use_is_critical() -> None:
    findings = finding_for(
        'row_fingerprint_for("data/trades/trades_master.parquet")'
    )
    assert any(
        item.classification == gov.FindingClassification.FINGERPRINT_V2_MISCLASSIFICATION
        and item.severity == gov.Severity.CRITICAL
        for item in findings
    )


def test_filesystem_replace_targeting_master_is_critical() -> None:
    findings = finding_for(
        'source.replace("data/trades/trades_master.parquet")'
    )
    assert any(
        item.classification == gov.FindingClassification.DIRECT_MASTER_WRITE
        and item.severity == gov.Severity.CRITICAL
        for item in findings
    )


def test_allow_evaluation_never_grants_operational_capabilities(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    registration = policy.registered_consumers[0]
    result = gov.evaluate_legacy_master_access(
        policy=policy,
        dataset_evidence=good_evidence(policy),
        request=gov.AccessRequest(
            consumer_path=registration.relative_path,
            purpose=registration.allowed_purposes[0],
            access_mode=gov.AccessMode.READ_ONLY,
        ),
    )
    assert result.allowed is True
    assert result.operational_authority is False
    assert result.import_eligible is False
    assert result.fingerprint_generation_allowed is False
    assert result.writes_trader_master is False


def test_legacy_writer_implementation_is_not_authorized_consumer(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    path = tmp_path / "smartcrypto" / "data" / "trades_importer.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def write_master():\n    raise RuntimeError\n", encoding="utf-8")
    findings, _ = gov.audit_legacy_master_consumers(
        project_root=tmp_path,
        policy=policy,
        tracked_inventory=gov.TrackedFileInventory(
            paths=("smartcrypto/data/trades_importer.py",),
            discovery_mode="fixture",
            complete=True,
        ),
    )
    assert [item.classification for item in findings] == [
        gov.FindingClassification.LEGACY_WRITER_IMPLEMENTATION
    ]


def test_comments_and_docstrings_are_not_executable_findings() -> None:
    source = (
        '"""data/trades/trades_master.parquet"""\n'
        "# data/trades/trades_master.parquet\n"
        "value = 1\n"
    )
    assert finding_for(source) == ()


def test_operational_configuration_reference_is_detected(tmp_path: Path) -> None:
    _, _, policy = setup_project(tmp_path)
    path = tmp_path / "config" / "live.yml"
    path.write_text("master: data/trades/trades_master.parquet\n", encoding="utf-8")
    findings, _ = gov.audit_legacy_master_consumers(
        project_root=tmp_path,
        policy=policy,
        tracked_inventory=gov.TrackedFileInventory(
            paths=("config/live.yml",),
            discovery_mode="fixture",
            complete=True,
        ),
    )
    assert findings[0].classification == gov.FindingClassification.CONFIGURATION_REFERENCE
    assert findings[0].severity == gov.Severity.CRITICAL


def test_dynamic_reference_makes_inventory_incomplete(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    source_path = tmp_path / "app.py"
    source_path.write_text(
        'name = "trades_master"\next = ".parquet"\npath = name + ext\n',
        encoding="utf-8",
    )
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        runner=fake_git(["app.py"]),
        generated_at_utc=FIXED_TIME,
    )
    assert report["dynamic_reference_unresolved_count"] >= 1
    assert report["consumer_inventory_complete"] is False


def test_git_ls_files_uses_shell_false_and_timeout(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        captured["argv"] = args[0]
        return subprocess.CompletedProcess(args[0], 0, "a.py\0", "")

    inventory = gov.discover_tracked_files(tmp_path, runner=runner, timeout_seconds=3.0)
    assert captured["argv"] == ["git", "ls-files", "-z"]
    assert captured["shell"] is False
    assert captured["timeout"] == 3.0
    assert inventory.paths == ("a.py",)


def test_git_timeout_is_controlled(tmp_path: Path) -> None:
    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args[0], 1)

    with pytest.raises(gov.TrackedFileDiscoveryError, match="git_ls_files_timeout"):
        gov.discover_tracked_files(tmp_path, runner=runner)


def test_default_does_not_write(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        runner=fake_git(),
        generated_at_utc=FIXED_TIME,
    )
    assert report["write_performed"] is False
    assert not (tmp_path / "data" / "reports").exists()


def test_write_report_is_limited_to_data_reports(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        write_report=True,
        runner=fake_git(),
        generated_at_utc=FIXED_TIME,
    )
    assert report["write_performed"] is True
    assert (tmp_path / gov.DEFAULT_JSON_REPORT).is_file()
    assert (tmp_path / gov.DEFAULT_MARKDOWN_REPORT).is_file()


def test_write_report_rejects_output_outside_data_reports(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        write_report=True,
        output_json="outside.json",
        output_markdown="outside.md",
        runner=fake_git(),
    )
    assert report["status"] == "blocked"
    assert report["write_performed"] is False


def test_json_and_markdown_preserve_prohibitive_flags(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    report = gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        write_report=True,
        runner=fake_git(),
        generated_at_utc=FIXED_TIME,
    )
    payload = json.loads((tmp_path / gov.DEFAULT_JSON_REPORT).read_text(encoding="utf-8"))
    markdown = (tmp_path / gov.DEFAULT_MARKDOWN_REPORT).read_text(encoding="utf-8")
    for field, expected in gov.SAFETY_FLAGS.items():
        assert report[field] is expected
        assert payload[field] is expected
    assert "no identity, deduplication, training, risk" in markdown


def test_output_is_deterministic_except_generated_at(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    kwargs = {
        "project_root": tmp_path,
        "policy_path": policy_path,
        "trader_master_path": master,
        "runner": fake_git(),
    }
    first = gov.build_legacy_master_boundary_report(
        **kwargs,
        generated_at_utc="2026-07-13T00:00:00+00:00",
    )
    second = gov.build_legacy_master_boundary_report(
        **kwargs,
        generated_at_utc="2026-07-14T00:00:00+00:00",
    )
    first.pop("generated_at_utc")
    second.pop("generated_at_utc")
    assert first == second


def test_legacy_writer_source_is_never_executed(tmp_path: Path) -> None:
    master, policy_path, _ = setup_project(tmp_path)
    writer = tmp_path / "smartcrypto" / "data" / "trades_importer.py"
    writer.parent.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "executed.txt"
    writer.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    gov.build_legacy_master_boundary_report(
        project_root=tmp_path,
        policy_path=policy_path,
        trader_master_path=master,
        runner=fake_git(["smartcrypto/data/trades_importer.py"]),
        generated_at_utc=FIXED_TIME,
    )
    assert not marker.exists()


def test_master_and_fingerprint_spec_remain_unchanged_during_audit() -> None:
    master = ROOT / gov.DEFAULT_MASTER
    fingerprint_spec = ROOT / "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
    before = (sha256(master), sha256(fingerprint_spec))
    report = gov.build_legacy_master_boundary_report(
        project_root=ROOT,
        runner=fake_git(),
        generated_at_utc=FIXED_TIME,
    )
    assert report["writes_trader_master"] is False
    assert (sha256(master), sha256(fingerprint_spec)) == before


def test_cli_no_write_json_executes_without_pythonpath() -> None:
    completed = subprocess.run(  # nosec B603 - fixed local interpreter and script
        [sys.executable, str(SCRIPT), "--project-root", str(ROOT), "--no-write", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] in {"ok", "blocked"}
    assert payload["write_performed"] is False
    assert payload["operational_authority"] is False
