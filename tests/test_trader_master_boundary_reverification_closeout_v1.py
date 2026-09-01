from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (
    build_legacy_master_boundary_report,
)
from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_remediation_plan import (
    build_remediation_plan_report,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_RELATIVE = Path("config/trader_master_legacy_research_only_policy_v1.json")
TAXONOMY_RELATIVE = Path(
    "config/trader_master_legacy_boundary_remediation_taxonomy_v1.json"
)
FINGERPRINT_SPEC_RELATIVE = Path(
    "smartcrypto/data/trader_master_fingerprint_v2/fingerprint_spec.py"
)
LEGACY_WRITER_RELATIVE = Path("smartcrypto/data/trades_importer.py")
MASTER_RELATIVE = Path("data/trades/trades_master.parquet")
MASTER_PINNED_SHA256 = (
    "24e049b3ca7a72dbde071a056548035fed87651d48959cd0cf4c6c8b0dac7295"
)
FINGERPRINT_SPEC_SHA256 = (
    "7efee2c2ac682242796ac9954ddea525cd34c4a69ab985cdefcdb4e5fe223147"
)
POLICY_SHA256 = (
    "6c81df43f594f063c4e0aa346a2e7d1ef5ff8088463fbe5a4d1c07cd8606967e"
)
FIXED_TIME = "2026-07-14T00:00:00+00:00"


@dataclass(frozen=True)
class SyntheticCloseout:
    root: Path
    master: Path
    policy: Path
    fingerprint_spec: Path
    boundary: dict[str, Any]
    plan: dict[str, Any]
    protected_hashes_before: dict[str, str]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_git(paths: tuple[str, ...]) -> Any:
    output = "\0".join(paths) + ("\0" if paths else "")

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(args[0], 0, output, "")

    return runner


def build_synthetic_closeout(root: Path) -> SyntheticCloseout:
    policy_payload = json.loads(
        (ROOT / POLICY_RELATIVE).read_text(encoding="utf-8")
    )
    expected_columns = [str(item) for item in policy_payload["expected_schema_columns"]]

    master = root / MASTER_RELATIVE
    master.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                column: f"synthetic-{index}"
                for index, column in enumerate(expected_columns)
            }
        ]
    )
    frame.to_parquet(master, index=False)

    policy_payload.update(
        expected_sha256=sha256(master),
        expected_size_bytes=master.stat().st_size,
        expected_row_count=len(frame),
        expected_schema_columns=list(frame.columns),
    )
    policy = root / POLICY_RELATIVE
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        json.dumps(policy_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    taxonomy = root / TAXONOMY_RELATIVE
    taxonomy.parent.mkdir(parents=True, exist_ok=True)
    taxonomy.write_bytes((ROOT / TAXONOMY_RELATIVE).read_bytes())

    fingerprint_spec = root / FINGERPRINT_SPEC_RELATIVE
    fingerprint_spec.parent.mkdir(parents=True, exist_ok=True)
    fingerprint_spec.write_bytes((ROOT / FINGERPRINT_SPEC_RELATIVE).read_bytes())

    legacy_writer = root / LEGACY_WRITER_RELATIVE
    legacy_writer.parent.mkdir(parents=True, exist_ok=True)
    legacy_writer.write_text(
        "from __future__ import annotations\n\n"
        "class QuarantinedLegacyWriter:\n"
        "    pass\n",
        encoding="utf-8",
    )

    protected_hashes_before = {
        "master": sha256(master),
        "policy": sha256(policy),
        "fingerprint_spec": sha256(fingerprint_spec),
    }
    tracked_paths = (LEGACY_WRITER_RELATIVE.as_posix(),)
    boundary = build_legacy_master_boundary_report(
        project_root=root,
        write_report=False,
        generated_at_utc=FIXED_TIME,
        runner=fake_git(tracked_paths),
    )
    plan = build_remediation_plan_report(
        project_root=root,
        write_report=False,
        generated_at_utc=FIXED_TIME,
        source_boundary_report=boundary,
        source_commit_sha="0" * 40,
        source_branch="synthetic-closeout",
        runner=fake_git(()),
    )
    return SyntheticCloseout(
        root=root,
        master=master,
        policy=policy,
        fingerprint_spec=fingerprint_spec,
        boundary=boundary,
        plan=plan,
        protected_hashes_before=protected_hashes_before,
    )


@pytest.fixture(scope="module")
def closeout(tmp_path_factory: pytest.TempPathFactory) -> SyntheticCloseout:
    return build_synthetic_closeout(
        tmp_path_factory.mktemp("trader-master-boundary-closeout")
    )


def test_final_boundary_has_no_unresolved_violation(
    closeout: SyntheticCloseout,
) -> None:
    boundary = closeout.boundary
    for field in (
        "high_count",
        "critical_count",
        "dynamic_reference_unresolved_count",
        "direct_import_count",
        "direct_write_count",
        "legacy_writer_callsite_count",
        "operational_consumer_count",
        "unregistered_consumer_count",
    ):
        assert boundary[field] == 0
    assert boundary["status"] == "ok"
    assert boundary["consumer_inventory_complete"] is True
    assert boundary["decision"] == "LEGACY_MASTER_SEGREGATED_RESEARCH_ONLY"
    assert boundary["segregation_enforced"] is True


def test_final_boundary_preserves_all_safety_denials(
    closeout: SyntheticCloseout,
) -> None:
    boundary = closeout.boundary
    for field in (
        "writes_trader_master",
        "import_authorized",
        "write_authorized",
        "fingerprint_generation_allowed",
        "operational_training_authorized",
        "paper_signal_selection_authorized",
        "live_signal_selection_authorized",
        "risk_decision_authorized",
        "order_execution_authorized",
        "operational_authority",
        "sends_orders",
        "exchange_private_access",
    ):
        assert boundary[field] is False


def test_legacy_writer_implementation_remains_quarantined_without_callsites(
    closeout: SyntheticCloseout,
) -> None:
    boundary = closeout.boundary
    assert boundary["legacy_writer_implementation_count"] == 1
    assert boundary["legacy_writer_callsite_count"] == 0
    assert boundary["direct_import_count"] == 0
    assert boundary["direct_write_count"] == 0


def test_planner_requires_no_follow_up_package_or_policy_change(
    closeout: SyntheticCloseout,
) -> None:
    report = closeout.plan

    assert report["status"] == "ok"
    assert report["decision"] == "LEGACY_BOUNDARY_REMEDIATION_NOT_REQUIRED"
    assert report["reason"] == "legacy_boundary_already_segregated"
    assert report["branch_package_count"] == 0
    assert report["readonly_consumer_registration_count"] == 0
    assert report["planned_policy_change_count"] == 0


def test_protected_artifacts_remain_byte_identical(
    closeout: SyntheticCloseout,
) -> None:
    versioned_policy = ROOT / POLICY_RELATIVE
    policy_payload = json.loads(versioned_policy.read_text(encoding="utf-8"))

    assert sha256(ROOT / FINGERPRINT_SPEC_RELATIVE) == FINGERPRINT_SPEC_SHA256
    assert sha256(versioned_policy) == POLICY_SHA256
    assert policy_payload["expected_sha256"] == MASTER_PINNED_SHA256

    assert sha256(closeout.master) == closeout.protected_hashes_before["master"]
    assert sha256(closeout.policy) == closeout.protected_hashes_before["policy"]
    assert (
        sha256(closeout.fingerprint_spec)
        == closeout.protected_hashes_before["fingerprint_spec"]
    )
