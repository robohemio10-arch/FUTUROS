from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smartcrypto.ops.saas_tenant_security_baseline import build_saas_tenant_security_baseline


def write_json(root: Path, relative: str, payload: dict) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def valid_registry() -> dict:
    return {
        "tenants": [
            {
                "tenant_id": "tenant_alpha",
                "display_name": "Tenant Alpha",
                "environment": "paper",
                "data_namespace": "tenant_alpha_data",
                "runtime_namespace": "tenant_alpha_runtime",
                "runtime_mutation_allowed": False,
                "cross_tenant_data_access_allowed": False,
                "exchange_private_access": False,
            },
            {
                "tenant_id": "tenant_beta",
                "display_name": "Tenant Beta",
                "environment": "shadow",
                "data_namespace": "tenant_beta_data",
                "runtime_namespace": "tenant_beta_runtime",
                "runtime_mutation_allowed": False,
                "cross_tenant_data_access_allowed": False,
                "exchange_private_access": False,
            },
        ]
    }


def valid_policy() -> dict:
    return {
        "tenant_isolation_required": True,
        "rbac_required": True,
        "audit_trail_required": True,
        "admin_read_only_separation_required": True,
        "secret_hygiene_required": True,
        "runtime_data_boundary_required": True,
        "cross_tenant_leakage_prevention_required": True,
        "paper_shadow_only_required": True,
        "tenant_runtime_mutation_allowed": False,
        "cross_tenant_data_access_allowed": False,
        "shared_runtime_namespace_allowed": False,
        "shared_data_namespace_allowed": False,
        "plaintext_secret_allowed": False,
        "live_trading_enabled": False,
        "exchange_private_access": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "changes_risk": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "roles": {
            "viewer": {"permissions": ["read_reports"]},
            "operator": {"permissions": ["read_reports", "run_read_only_audits"]},
            "admin": {"permissions": ["read_reports", "run_read_only_audits", "manage_users"]},
        },
    }


def seed_valid_baseline(root: Path) -> None:
    write_json(root, "config/saas/tenant_registry.json", valid_registry())
    write_json(root, "config/saas/access_policy.json", valid_policy())


def test_missing_config_is_warning_only_baseline(tmp_path: Path) -> None:
    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "baseline_defined_with_warnings"
    assert "tenant_registry_missing_baseline_only" in result.report["warning_reasons"]
    assert "access_policy_missing_baseline_only" in result.report["warning_reasons"]
    assert result.report["paper_only"] is True
    assert result.report["shadow_only"] is True
    assert result.report["live_release_allowed"] is False
    assert result.report["canary_release_allowed"] is False


def test_valid_registry_and_policy_define_baseline(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)

    result = build_saas_tenant_security_baseline(
        project_root=tmp_path,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["status"] == "baseline_defined"
    assert result.report["blocking_reasons"] == []
    assert result.report["baseline"]["tenant_isolation_required"] is True
    assert result.report["tenant_runtime_mutation_allowed"] is False
    assert result.report["sends_orders"] is False


def test_live_environment_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/tenant_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tenants"][0]["environment"] = "live"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("environment_prohibited" in reason for reason in result.report["blocking_reasons"])


def test_duplicate_tenant_id_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/tenant_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tenants"][1]["tenant_id"] = "tenant_alpha"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "tenant_registry_duplicate_tenant_id: tenant_alpha" in result.report["blocking_reasons"]


def test_duplicate_namespace_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/tenant_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tenants"][1]["data_namespace"] = "tenant_alpha_data"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "tenant_registry_duplicate_data_namespace: tenant_alpha_data" in result.report["blocking_reasons"]


def test_runtime_mutation_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/tenant_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tenants"][0]["runtime_mutation_allowed"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("runtime_mutation_allowed" in reason for reason in result.report["blocking_reasons"])


def test_policy_missing_required_true_flag_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/access_policy.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rbac_required"] = False
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "access_policy_rbac_required_must_be_true" in result.report["blocking_reasons"]


def test_policy_true_forbidden_flag_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/access_policy.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["exchange_private_access"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("exchange_private_access" in reason for reason in result.report["blocking_reasons"])


def test_prohibited_role_permission_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/access_policy.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["roles"]["admin"]["permissions"].append("submit_orders")
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert any("prohibited_permissions" in reason for reason in result.report["blocking_reasons"])


def test_missing_role_blocks(tmp_path: Path) -> None:
    seed_valid_baseline(tmp_path)
    path = tmp_path / "config/saas/access_policy.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["roles"]["operator"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=True)

    assert result.report["status"] == "blocked"
    assert "access_policy_missing_roles: operator" in result.report["blocking_reasons"]


def test_write_enabled_creates_report(tmp_path: Path) -> None:
    result = build_saas_tenant_security_baseline(project_root=tmp_path, no_write=False)

    assert result.write_performed is True
    assert result.output_path.exists()
    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "saas_tenant_security_baseline_v1"
    assert payload["paper_only"] is True
    assert payload["sends_orders"] is False


def test_generated_at_is_stable(tmp_path: Path) -> None:
    result = build_saas_tenant_security_baseline(
        project_root=tmp_path,
        no_write=True,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert result.report["generated_at"] == "2026-01-01T00:00:00Z"
