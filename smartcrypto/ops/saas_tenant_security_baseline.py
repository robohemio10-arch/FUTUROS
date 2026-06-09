from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "saas_tenant_security_baseline_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/saas_tenant_security_baseline.json")
DEFAULT_TENANT_REGISTRY_PATH = Path("config/saas/tenant_registry.json")
DEFAULT_ACCESS_POLICY_PATH = Path("config/saas/access_policy.json")

TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
TENANT_ALLOWED_ENVIRONMENTS = {"paper", "shadow"}
TENANT_PROHIBITED_ENVIRONMENTS = {"live", "production", "prod", "real"}
REQUIRED_TENANT_FIELDS = ("tenant_id", "display_name", "environment", "data_namespace", "runtime_namespace")
REQUIRED_TRUE_POLICY_FLAGS = (
    "tenant_isolation_required",
    "rbac_required",
    "audit_trail_required",
    "admin_read_only_separation_required",
    "secret_hygiene_required",
    "runtime_data_boundary_required",
    "cross_tenant_leakage_prevention_required",
    "paper_shadow_only_required",
)
REQUIRED_FALSE_POLICY_FLAGS = (
    "tenant_runtime_mutation_allowed",
    "cross_tenant_data_access_allowed",
    "shared_runtime_namespace_allowed",
    "shared_data_namespace_allowed",
    "plaintext_secret_allowed",
    "live_trading_enabled",
    "exchange_private_access",
    "order_submission_enabled",
    "real_order_submission_enabled",
    "sends_orders",
    "changes_risk",
    "live_release_allowed",
    "canary_release_allowed",
)
PROHIBITED_TRUE_KEYS = set(REQUIRED_FALSE_POLICY_FLAGS) | {
    "release_allowed",
    "auto_promotion_allowed",
    "writes_trades_master",
    "changes_training_dataset",
    "changes_model",
    "promotes_model",
}


@dataclass(frozen=True)
class BaselineResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_saas_tenant_security_baseline(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    tenant_registry_path: str | Path = DEFAULT_TENANT_REGISTRY_PATH,
    access_policy_path: str | Path = DEFAULT_ACCESS_POLICY_PATH,
    no_write: bool = False,
    now: datetime | None = None,
) -> BaselineResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    registry_path = resolve_under_root(root, tenant_registry_path)
    policy_path = resolve_under_root(root, access_policy_path)
    current_time = now or datetime.now(timezone.utc)

    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    next_required_actions: list[str] = [
        "Manter SaaS em baseline auditável até revisão explícita de isolamento multi-tenant.",
        "Não habilitar execução real, acesso privado à exchange ou mutação de risco por tenant.",
    ]

    registry_payload: Mapping[str, Any] | None = None
    policy_payload: Mapping[str, Any] | None = None

    if registry_path.exists():
        try:
            registry_payload = load_json_object(registry_path)
            blocking_reasons.extend(validate_tenant_registry(registry_payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            blocking_reasons.append(f"tenant_registry_invalid: {type(exc).__name__}: {exc}")
    else:
        warning_reasons.append("tenant_registry_missing_baseline_only")

    if policy_path.exists():
        try:
            policy_payload = load_json_object(policy_path)
            blocking_reasons.extend(validate_access_policy(policy_payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            blocking_reasons.append(f"access_policy_invalid: {type(exc).__name__}: {exc}")
    else:
        warning_reasons.append("access_policy_missing_baseline_only")

    for label, payload in (("tenant_registry", registry_payload), ("access_policy", policy_payload)):
        if payload is not None:
            blocking_reasons.extend(collect_policy_violations(label, payload))

    if blocking_reasons:
        status = "blocked"
    elif warning_reasons:
        status = "baseline_defined_with_warnings"
    else:
        status = "baseline_defined"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "tenant_registry_path": str(registry_path),
        "access_policy_path": str(policy_path),
        "tenant_registry_summary": summarize_tenant_registry(registry_payload),
        "access_policy_summary": summarize_access_policy(policy_payload),
        "baseline": build_baseline_contract(),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": sorted(set(next_required_actions)),
        "paper_only": True,
        "shadow_only": True,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "release_allowed": False,
        "tenant_runtime_mutation_allowed": False,
        "cross_tenant_data_access_allowed": False,
        "exchange_private_access": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "sends_orders": False,
        "changes_risk": False,
        "writes_trades_master": False,
        "changes_training_dataset": False,
        "changes_model": False,
        "promotes_model": False,
        "tenant_registry_template": tenant_registry_template(),
        "access_policy_template": access_policy_template(),
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return BaselineResult(report=report, output_path=output_path, write_performed=write_performed)


def validate_tenant_registry(payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    tenants_value = payload.get("tenants")
    if not isinstance(tenants_value, list):
        return ["tenant_registry_tenants_must_be_list"]

    tenant_ids: set[str] = set()
    data_namespaces: set[str] = set()
    runtime_namespaces: set[str] = set()

    for index, tenant in enumerate(tenants_value):
        if not isinstance(tenant, Mapping):
            violations.append(f"tenant_registry_tenant_{index}_must_be_object")
            continue

        missing = [field for field in REQUIRED_TENANT_FIELDS if not normalize(tenant.get(field))]
        if missing:
            violations.append(f"tenant_registry_tenant_{index}_missing_fields: {','.join(missing)}")

        tenant_id = normalize(tenant.get("tenant_id"))
        if tenant_id:
            if not TENANT_ID_PATTERN.match(tenant_id):
                violations.append(f"tenant_registry_tenant_id_invalid: {tenant_id}")
            if tenant_id in tenant_ids:
                violations.append(f"tenant_registry_duplicate_tenant_id: {tenant_id}")
            tenant_ids.add(tenant_id)

        environment = normalize(tenant.get("environment")).lower()
        if environment in TENANT_PROHIBITED_ENVIRONMENTS:
            violations.append(f"tenant_registry_tenant_{tenant_id or index}_environment_prohibited: {environment}")
        elif environment and environment not in TENANT_ALLOWED_ENVIRONMENTS:
            violations.append(f"tenant_registry_tenant_{tenant_id or index}_environment_not_allowed: {environment}")

        data_namespace = normalize(tenant.get("data_namespace"))
        runtime_namespace = normalize(tenant.get("runtime_namespace"))
        if data_namespace:
            if data_namespace in data_namespaces:
                violations.append(f"tenant_registry_duplicate_data_namespace: {data_namespace}")
            data_namespaces.add(data_namespace)
        if runtime_namespace:
            if runtime_namespace in runtime_namespaces:
                violations.append(f"tenant_registry_duplicate_runtime_namespace: {runtime_namespace}")
            runtime_namespaces.add(runtime_namespace)

        if is_truthy(tenant.get("runtime_mutation_allowed")):
            violations.append(f"tenant_registry_tenant_{tenant_id or index}_runtime_mutation_allowed")
        if is_truthy(tenant.get("cross_tenant_data_access_allowed")):
            violations.append(f"tenant_registry_tenant_{tenant_id or index}_cross_tenant_data_access_allowed")
        if is_truthy(tenant.get("exchange_private_access")):
            violations.append(f"tenant_registry_tenant_{tenant_id or index}_exchange_private_access_true")

    return sorted(set(violations))


def validate_access_policy(payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for key in REQUIRED_TRUE_POLICY_FLAGS:
        if payload.get(key) is not True:
            violations.append(f"access_policy_{key}_must_be_true")
    for key in REQUIRED_FALSE_POLICY_FLAGS:
        if is_truthy(payload.get(key)):
            violations.append(f"access_policy_{key}_must_be_false")

    roles = payload.get("roles")
    if roles is not None:
        violations.extend(validate_roles(roles))

    return sorted(set(violations))


def validate_roles(roles: Any) -> list[str]:
    violations: list[str] = []
    if not isinstance(roles, Mapping):
        return ["access_policy_roles_must_be_object"]

    required_roles = {"viewer", "operator", "admin"}
    missing_roles = sorted(required_roles.difference(str(role) for role in roles))
    if missing_roles:
        violations.append("access_policy_missing_roles: " + ",".join(missing_roles))

    for role_name, role_payload in roles.items():
        if not isinstance(role_payload, Mapping):
            violations.append(f"access_policy_role_{role_name}_must_be_object")
            continue
        permissions = role_payload.get("permissions", [])
        if not isinstance(permissions, list):
            violations.append(f"access_policy_role_{role_name}_permissions_must_be_list")
            continue
        normalized_permissions = {normalize(permission) for permission in permissions}
        prohibited = {
            "submit_orders",
            "enable_live",
            "mutate_risk",
            "read_private_exchange",
            "cross_tenant_read",
            "cross_tenant_write",
        }.intersection(normalized_permissions)
        if prohibited:
            violations.append(f"access_policy_role_{role_name}_prohibited_permissions: {','.join(sorted(prohibited))}")

    return violations


def collect_policy_violations(prefix: str, payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for key, value in iter_key_values(payload):
        if key in PROHIBITED_TRUE_KEYS and is_truthy(value):
            violations.append(f"{prefix}:{key}=true")
    return sorted(set(violations))


def build_baseline_contract() -> dict[str, Any]:
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
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def tenant_registry_template() -> dict[str, Any]:
    return {
        "tenants": [
            {
                "tenant_id": "tenant_demo",
                "display_name": "Tenant Demo",
                "environment": "paper",
                "data_namespace": "tenant_demo_data",
                "runtime_namespace": "tenant_demo_runtime",
                "runtime_mutation_allowed": False,
                "cross_tenant_data_access_allowed": False,
                "exchange_private_access": False,
            }
        ]
    }


def access_policy_template() -> dict[str, Any]:
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


def summarize_tenant_registry(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    tenants = payload.get("tenants")
    if not isinstance(tenants, list):
        return {"tenants_count": None}
    return {
        "tenants_count": len(tenants),
        "tenant_ids": [tenant.get("tenant_id") for tenant in tenants if isinstance(tenant, Mapping)],
        "environments": sorted(
            {
                normalize(tenant.get("environment")).lower()
                for tenant in tenants
                if isinstance(tenant, Mapping) and normalize(tenant.get("environment"))
            }
        ),
    }


def summarize_access_policy(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    roles = payload.get("roles")
    return {
        "rbac_required": payload.get("rbac_required"),
        "tenant_isolation_required": payload.get("tenant_isolation_required"),
        "roles": sorted(str(role) for role in roles) if isinstance(roles, Mapping) else None,
    }


def load_json_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iter_key_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key), nested
            if isinstance(nested, (Mapping, list, tuple)):
                yield from iter_key_values(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_key_values(item)
