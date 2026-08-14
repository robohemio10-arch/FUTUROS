from __future__ import annotations

from pathlib import Path
from typing import Any

from smartcrypto.dashboard.controls.command_classifier import list_dashboard_command_policies
from smartcrypto.dashboard.security.dashboard_readonly_guard import assert_dashboard_readonly
from smartcrypto.ops.dashboard_snapshots.contracts import DashboardAuditContract
from tests.dashboard_page_test_support import FakeUi, load_page_module


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "smartcrypto" / "dashboard" / "pages" / "06_active_controls.py"


def _load_page():
    return load_page_module(PAGE)


def _snapshot(module: Any) -> dict[str, Any]:
    sections = {
        name: {"status": "OK", "reason": "fixture"}
        for name in module.REQUIRED_SECTIONS
    }
    sections["active_layer_status"].update(
        {"command_execution_enabled": False, "paper_entry_allowed": False}
    )
    sections["kill_switch"].update(
        {"global_kill_switch_active": False, "kill_switch_effective": False}
    )
    sections["security_state"].update(
        {
            "reconciliation_lock_active": True,
            "riskmanager_authority": True,
            "live_authority": False,
            "real_order_submission_enabled": False,
        }
    )
    sections["readiness_gap_accounting"].update(
        {
            "status": "BLOCKED",
            "seven_day_diagnostic_status": "OK",
            "thirty_day_readiness_status": "BLOCKED",
            "continuous_valid_soak_days": 4.5,
            "observed_calendar_days": 7,
            "critical_gap_count": 2,
            "warning_gap_count": 1,
            "max_gap_minutes": 19.0,
            "readiness_gap_free": False,
            "manual_go_no_go_required": True,
            "canary_release_allowed": False,
            "live_release_allowed": False,
        }
    )
    sections["paper_runtime_health"].update(
        {
            "status": "WARNING",
            "paper_runtime_alive": True,
            "paper_runtime_fresh": True,
            "critical_stale_count": 0,
            "warning_stale_count": 1,
            "container_collection_requested": False,
            "container_snapshot_status": "disabled",
            "docker_services_status": "disabled",
            "freqtrade_paper_status": "ok",
            "smartcrypto_bot_status": "ok",
            "canary_release_allowed": False,
            "live_release_allowed": False,
        }
    )
    sections["runtime_evidence_integration"].update(
        {
            "status": "BLOCKED",
            "runtime_evidence_status": "BLOCKED",
            "runtime_evidence_pack_status": "BLOCKED",
            "readiness_status": "BLOCKED",
            "paper_runtime_health_status": "WARNING",
            "container_snapshot_status": "DISABLED",
            "soak_status": "BLOCKED",
            "gap_accounting_status": "BLOCKED",
            "continuous_valid_soak_days": 4.5,
            "required_soak_days": 30,
            "critical_gap_count": 2,
            "evidence_sources_blocked": 2,
            "evidence_sources_missing": 1,
            "evidence_sources_stale": 0,
            "canary_release_allowed": False,
            "live_release_allowed": False,
        }
    )
    sections["audit"].update(
        {"dashboard_reads_only": True, "changes_config": False}
    )
    return {
        "schema_version": module.EXPECTED_SCHEMA_VERSION,
        "runtime_mode": "paper",
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "last_updated_utc": "2026-08-14T18:00:00Z",
        "status_summary": {"status": "BLOCKED"},
        "missing_required_sources": [],
        "missing_optional_sources": [],
        "audit": DashboardAuditContract(snapshot_source="aba06_visual_fixture").to_dict(),
        "sections": sections,
    }


def _rendered(ui: FakeUi) -> str:
    return "\n".join(str(value) for _name, value in ui.events)


def _html(ui: FakeUi) -> list[str]:
    return [
        str(value)
        for name, value in ui.events
        if name == "markdown" and isinstance(value, str)
    ]


def test_page_contract_constants_are_stable() -> None:
    module = _load_page()
    assert module.PAGE_TITLE == "06. Controles Ativos"
    assert module.SNAPSHOT_PATH == "data/reports/dashboard_active_controls_snapshot.json"
    assert module.EXPECTED_SCHEMA_VERSION == "dashboard_active_controls_snapshot_v1"
    assert len(module.REQUIRED_SECTIONS) >= 20


def test_visual_fixture_satisfies_dashboard_readonly_guard_contract() -> None:
    module = _load_page()
    snapshot = _snapshot(module)

    assert snapshot["audit"]["dashboard_reads_only"] is True
    assert_dashboard_readonly(snapshot)


def test_primary_grid_has_exactly_six_institutional_kpis() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    kpis = [value for value in _html(ui) if value.startswith('<div class="sfc-mini-kpi ')]
    assert len(kpis) == 6
    rendered = "\n".join(kpis)
    for label in (
        "Command Execution",
        "Paper Entry Allowed",
        "Kill Switch",
        "RiskManager Authority",
        "30d Readiness",
        "N4 Hard Blocks",
    ):
        assert label in rendered


def test_execution_and_release_boundaries_are_fail_closed() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    rendered = _rendered(ui)
    assert "Command Execution" in rendered
    assert "DISABLED" in rendered
    assert "RiskManager Authority" in rendered
    assert "30d Readiness" in rendered
    assert "HARD-BLOCKED" in rendered
    assert "canary_release_allowed" in rendered
    assert "live_release_allowed" in rendered
    assert "false" in rendered


def test_policy_matrix_covers_all_levels_and_complete_n4_catalog() -> None:
    module = _load_page()
    policies = [policy.to_dict() for policy in list_dashboard_command_policies()]
    rows = module._policy_level_rows(policies)
    by_level = {row["Nível"]: row for row in rows}
    assert set(by_level) == {"N1", "N2", "N3", "N4"}
    assert by_level["N1"]["Status"] == "READONLY"
    assert by_level["N2"]["Modo"] == "DRY-RUN STUB"
    assert by_level["N3"]["Modo"] == "SENSITIVE DRY-RUN"
    assert by_level["N4"]["Status"] == "HARD_BLOCKED"
    assert by_level["N4"]["Autoridade"] == "none"
    assert by_level["N4"]["Políticas"] == sum(1 for policy in policies if policy["hard_blocked"])
    assert by_level["N4"]["Políticas"] > len(module.LEVEL4_ALWAYS_BLOCKED)


def test_dry_run_examples_never_execute() -> None:
    module = _load_page()
    rows = module._dry_run_rows()
    assert len(rows) == 3
    assert {row["Executado"] for row in rows} == {"false"}
    assert all(row["Efeito simulado"] != "UNKNOWN" for row in rows)
    assert {row["Nível"] for row in rows} == {
        "N1_LOCAL_INFO",
        "N2_DRY_RUN_STUB",
        "N3_DRY_RUN_STUB_SENSITIVE",
    }


def test_readiness_panel_preserves_7d_30d_and_release_booleans() -> None:
    module = _load_page()
    readiness = _snapshot(module)["sections"]["readiness_gap_accounting"]
    values = {row["Gate"]: row["Valor"] for row in module._readiness_rows(readiness)}
    assert values["7d diagnostic"] == "OK"
    assert values["30d readiness"] == "BLOCKED"
    assert values["Critical gaps"] == "2"
    assert values["Canary release allowed"] == "false"
    assert values["Live release allowed"] == "false"
    assert module._readiness_status(readiness) == "BLOCKED"


def test_safety_boundary_blocks_live_authority_or_real_orders() -> None:
    module = _load_page()
    active = {"status": "OK", "command_execution_enabled": False}
    kill = {"status": "OK", "global_kill_switch_active": False}
    safe = {
        "status": "OK",
        "riskmanager_authority": True,
        "live_authority": False,
        "real_order_submission_enabled": False,
    }
    assert module._safety_boundary_status(active, kill, safe) == "ok"
    unsafe = dict(safe)
    unsafe["live_authority"] = True
    assert module._safety_boundary_status(active, kill, unsafe) == "BLOCKED"


def test_runtime_health_and_evidence_do_not_synthesize_missing_values() -> None:
    module = _load_page()
    snapshot = _snapshot(module)
    runtime_values = {
        row["Sinal"]: row["Valor"]
        for row in module._paper_runtime_rows(snapshot["sections"]["paper_runtime_health"])
    }
    evidence_values = {
        row["Evidência"]: row["Valor"]
        for row in module._runtime_evidence_rows(snapshot["sections"]["runtime_evidence_integration"])
    }
    assert runtime_values["Paper runtime alive"] == "true"
    assert runtime_values["Container snapshot status"] == "disabled"
    assert evidence_values["Required soak days"] == "30"
    assert evidence_values["Evidence sources missing"] == "1"
    assert all(row["Valor"] == "UNKNOWN" for row in module._runtime_evidence_rows({}))


def test_runtime_blocker_summary_uses_existing_section_statuses() -> None:
    module = _load_page()
    sections = _snapshot(module)["sections"]
    sections["runtime_blockers_remediation"] = {"status": "BLOCKED", "reason": "fixture_blocker"}
    sections["runtime_source_health"] = {"status": "WARNING", "reason": "fixture_warning"}
    rows = module._runtime_blocker_rows(sections)
    assert any(
        row["Domínio"] == "Remediation"
        and row["Status"] == "BLOCKED"
        and row["Reason"] == "fixture_blocker"
        for row in rows
    )
    assert module._runtime_blocker_status(sections) == "blocked"


def test_canonical_legacy_renderers_remain_available_but_collapsed() -> None:
    module = _load_page()
    ui = FakeUi()
    module.render_page(_snapshot(module), ui=ui)
    rendered = _rendered(ui)
    assert "Detalhamento canônico da Aba 06" in rendered
    assert "STUB ONLY" in rendered
    assert "N4 HARD_BLOCKED" in rendered
    assert "Readiness & Gates" in rendered
    assert "LIVE_ORDER: disabled (HARD_BLOCKED)" in rendered


def test_minimum_snapshot_fails_closed_without_crashing() -> None:
    module = _load_page()
    snapshot = {
        "schema_version": module.EXPECTED_SCHEMA_VERSION,
        "runtime_mode": "paper",
        "dashboard_readonly": True,
        "paper_only": True,
        "shadow_only": True,
        "live_locked": True,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "audit": DashboardAuditContract(snapshot_source="aba06_minimum_fixture").to_dict(),
        "sections": {
            name: {"status": "UNKNOWN", "reason": "fixture_missing"}
            for name in module.REQUIRED_SECTIONS
        },
    }
    ui = FakeUi()
    module.render_page(snapshot, ui=ui)
    rendered = _rendered(ui)
    assert "UNKNOWN" in rendered
    assert "Command Execution" in rendered
    assert "Control Authority Matrix" in rendered


def test_page_does_not_import_operational_mutation_surfaces() -> None:
    source = PAGE.read_text(encoding="utf-8")
    for token in (
        "create_order",
        "set_kill_switch",
        "ccxt",
        "freqtrade.client",
        "risk_manager import",
        "subprocess",
        "requests.",
        "exchange_private",
    ):
        assert token not in source


def test_page_preserves_global_dashboard_contracts() -> None:
    source = PAGE.read_text(encoding="utf-8")
    assert "load_page_snapshot" in source
    assert "render_snapshot_page" in source
    assert "render_chrome=False" in source
    assert 'if __name__ == "__main__":' in source
    assert "DashboardPageId.active_controls" in source

def test_paper_runtime_and_dry_run_panels_use_full_width_visual_contract() -> None:
    source = PAGE.read_text(encoding="utf-8")

    old_two_column_contract = (
        'left, right = target_ui.columns(2)\n'
        '    left.markdown(\n'
        '        render_section_panel(\n'
        '            "Paper Runtime Health"'
    )
    dry_run_full_width_contract = (
        'target_ui.markdown(\n'
        '        render_section_panel(\n'
        '            "Governed Dry-run Examples"'
    )

    assert old_two_column_contract not in source
    assert dry_run_full_width_contract in source

    module = _load_page()
    rows = module._dry_run_rows()
    assert rows
    assert all(row["Executado"] == "false" for row in rows)
    assert all(row["Efeito simulado"] != "UNKNOWN" for row in rows)
