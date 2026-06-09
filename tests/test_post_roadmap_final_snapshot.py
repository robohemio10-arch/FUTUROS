from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.audit_post_roadmap_final_snapshot import (
    REQUIRED_DOCS,
    REQUIRED_OPS_MODULES,
    REQUIRED_SCRIPTS,
    REQUIRED_TESTS,
    build_post_roadmap_final_snapshot_audit,
)


def seed_required_files(root: Path) -> None:
    for path in (*REQUIRED_DOCS, *REQUIRED_SCRIPTS, *REQUIRED_OPS_MODULES, *REQUIRED_TESTS):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.name == "POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md":
            continue
        target.write_text("# placeholder\n", encoding="utf-8")

    (root / "docs/POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md").write_text(
        "\n".join(
            [
                "não altera lógica runtime",
                "não autoriza live trading",
                "canonical-30d-soak-readiness-threshold-enforcement",
                "transitive-lock-docker-runtime-reproducibility",
                "zip-standalone-audit-fallback",
                "runtime-evidence-pack-and-readiness-snapshot-v2",
                "paper-shadow-soak-continuity-and-gap-accounting",
                "monte-carlo-no-trade-recovery-diagnostics",
                "ai-shadow-threshold-live-readiness-evidence",
                "manual-go-no-go-live-canary-governance",
                "live-canary-contract-with-hard-blocks",
                "saas-tenant-security-baseline",
                "paper_only=true",
                "shadow_only=true",
                "live_release_allowed=false",
                "canary_release_allowed=false",
                "release_allowed=false",
                "real_order_submission_enabled=false",
                "order_submission_enabled=false",
                "exchange_private_access=false",
                "sends_orders=false",
                "changes_risk=false",
            ]
        ),
        encoding="utf-8",
    )


def test_snapshot_audit_blocks_when_required_files_missing(tmp_path: Path) -> None:
    result = build_post_roadmap_final_snapshot_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert result.report["documentation_only_snapshot"] is True
    assert result.report["runtime_logic_changed"] is False
    assert any(reason.startswith("missing_path:") for reason in result.report["blocking_reasons"])


def test_snapshot_audit_ok_with_required_files_and_invariants(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_post_roadmap_final_snapshot_audit(
        project_root=tmp_path,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["status"] == "ok"
    assert result.report["blocking_reasons"] == []
    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["live_release_allowed"] is False
    assert result.report["sends_orders"] is False
    assert result.report["changes_risk"] is False


def test_snapshot_doc_missing_invariant_blocks(tmp_path: Path) -> None:
    seed_required_files(tmp_path)
    snapshot_path = tmp_path / "docs/POST_ROADMAP_FINAL_CONSOLIDATION_SNAPSHOT.md"
    text = snapshot_path.read_text(encoding="utf-8").replace("sends_orders=false", "")
    snapshot_path.write_text(text, encoding="utf-8")

    result = build_post_roadmap_final_snapshot_audit(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "snapshot_doc_missing_invariant:sends_orders=false" in result.report["blocking_reasons"]


def test_write_enabled_creates_report(tmp_path: Path) -> None:
    seed_required_files(tmp_path)

    result = build_post_roadmap_final_snapshot_audit(project_root=tmp_path, no_write=False)

    assert result.write_performed is True
    assert result.output_path.exists()
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "post_roadmap_final_consolidation_snapshot_v1"
    assert payload["status"] == "ok"
    assert payload["runtime_logic_changed"] is False
