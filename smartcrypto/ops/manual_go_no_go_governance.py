from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "manual_go_no_go_live_canary_governance_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/manual_go_no_go_live_canary_governance.json")
DEFAULT_DECISION_PATH = Path("data/governance/manual_go_no_go_live_canary_decision.json")
DEFAULT_MAX_DECISION_AGE_HOURS = 72

VALID_DECISIONS = {"GO", "NO_GO", "GO_WITH_RESTRICTIONS", "DEFER"}
REQUIRED_DECISION_FIELDS = ("decision", "decided_at", "decider", "evidence_pack_id", "rationale")

EVIDENCE_PATHS: dict[str, str] = {
    "runtime_evidence_pack_v2": "data/reports/runtime_evidence_pack_v2.json",
    "readiness_snapshot_v2": "data/reports/readiness_snapshot_v2.json",
    "paper_shadow_soak_continuity": "data/reports/paper_shadow_soak_continuity_audit.json",
    "monte_carlo_no_trade_recovery": "data/reports/monte_carlo_no_trade_recovery_diagnostics.json",
    "ai_shadow_threshold_readiness": "data/reports/ai_shadow_threshold_readiness_evidence.json",
}

PROHIBITED_TRUE_KEYS = {
    "auto_promotion_allowed",
    "release_allowed",
    "live_release_allowed",
    "canary_release_allowed",
    "sends_orders",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
    "changes_model",
    "promotes_model",
}


@dataclass(frozen=True)
class GovernanceResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


def build_manual_go_no_go_live_canary_governance(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    decision_path: str | Path = DEFAULT_DECISION_PATH,
    max_decision_age_hours: int = DEFAULT_MAX_DECISION_AGE_HOURS,
    no_write: bool = False,
    now: datetime | None = None,
) -> GovernanceResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    decision_file = resolve_under_root(root, decision_path)
    current_time = now or datetime.now(timezone.utc)

    payloads, missing_evidence, invalid_evidence, evidence_sources = load_evidence(root)
    decision_payload, decision_validation = load_and_validate_decision(
        decision_file=decision_file,
        current_time=current_time,
        max_decision_age_hours=max_decision_age_hours,
    )

    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    next_required_actions: list[str] = []

    blocking_reasons.extend(decision_validation["blocking_reasons"])
    blocking_reasons.extend(collect_policy_violations(payloads))

    if invalid_evidence:
        blocking_reasons.append("invalid_evidence_present")
    if not payloads:
        blocking_reasons.append("readiness_evidence_missing")
    elif missing_evidence:
        warning_reasons.append("readiness_evidence_incomplete")

    evidence_statuses = {normalize(payload.get("status")) for payload in payloads.values()}
    if evidence_statuses.intersection({"blocked", "error", "failed", "invalid", "evidence_missing"}):
        blocking_reasons.append("upstream_evidence_not_ready")

    decision = decision_validation["decision"]
    if decision == "NO_GO":
        blocking_reasons.append("manual_decision_no_go")
    elif decision == "DEFER":
        blocking_reasons.append("manual_decision_defer")
    elif decision == "GO_WITH_RESTRICTIONS":
        blocking_reasons.append("manual_decision_requires_restriction_contract")
        next_required_actions.append("Mapear restrições humanas em hard blocks antes da próxima etapa.")
    elif decision == "GO":
        next_required_actions.append("Validar contrato operacional com hard blocks antes da próxima etapa.")
    else:
        next_required_actions.append("Registrar decisão humana GO, NO_GO, GO_WITH_RESTRICTIONS ou DEFER.")

    if blocking_reasons:
        status = "blocked"
    elif warning_reasons:
        status = "manual_go_recorded_with_warnings"
    elif decision == "GO":
        status = "manual_go_recorded"
    else:
        status = "manual_review_required"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "manual_go_no_go_required": True,
        "manual_decision_status": decision_validation["status"],
        "manual_decision": decision,
        "manual_decision_path": str(decision_file),
        "auto_promotion_allowed": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "changes_risk": False,
        "sends_orders": False,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": sorted(set(next_required_actions)),
        "missing_evidence": sorted(missing_evidence),
        "invalid_evidence": invalid_evidence,
        "evidence_sources": evidence_sources,
        "decision_summary": summarize_decision(decision_payload),
        "decision_template": decision_template(),
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return GovernanceResult(report=report, output_path=output_path, write_performed=write_performed)


def load_evidence(root: Path) -> tuple[dict[str, Mapping[str, Any]], list[str], list[dict[str, str]], list[dict[str, Any]]]:
    payloads: dict[str, Mapping[str, Any]] = {}
    missing: list[str] = []
    invalid: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []

    for name, relative_path in EVIDENCE_PATHS.items():
        path = root / relative_path
        if not path.exists():
            missing.append(name)
            continue
        try:
            payload = load_json_object(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            invalid.append({"name": name, "path": relative_path, "error": f"{type(exc).__name__}: {exc}"})
            continue
        payloads[name] = payload
        sources.append({"name": name, "path": relative_path, "status": payload.get("status")})

    return payloads, missing, invalid, sources


def load_and_validate_decision(
    *,
    decision_file: Path,
    current_time: datetime,
    max_decision_age_hours: int,
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    if not decision_file.exists():
        return None, {"status": "missing", "decision": None, "blocking_reasons": ["manual_decision_missing"]}
    try:
        payload = load_json_object(decision_file)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, {
            "status": "invalid",
            "decision": None,
            "blocking_reasons": [f"invalid_decision_file: {type(exc).__name__}: {exc}"],
        }
    return payload, validate_manual_decision(payload, now=current_time, max_age_hours=max_decision_age_hours)


def validate_manual_decision(payload: Mapping[str, Any], *, now: datetime, max_age_hours: int) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    decision = normalize_decision(payload.get("decision"))

    missing_fields = [field for field in REQUIRED_DECISION_FIELDS if not normalize(payload.get(field))]
    if missing_fields:
        blocking_reasons.append("manual_decision_missing_required_fields: " + ",".join(missing_fields))
    if decision not in VALID_DECISIONS:
        blocking_reasons.append(f"manual_decision_invalid: {decision}")

    decided_at = parse_datetime(payload.get("decided_at"))
    if decided_at is None:
        blocking_reasons.append("manual_decision_decided_at_invalid")
    else:
        age = now - decided_at
        if age < timedelta(0):
            blocking_reasons.append("manual_decision_decided_at_in_future")
        if age > timedelta(hours=max_age_hours):
            blocking_reasons.append("manual_decision_expired")

    if payload.get("acknowledges_risk") is not True:
        blocking_reasons.append("manual_decision_must_acknowledge_risk")
    if payload.get("acknowledges_no_automatic_release") is not True:
        blocking_reasons.append("manual_decision_must_acknowledge_no_automatic_release")
    if decision == "GO_WITH_RESTRICTIONS" and not payload.get("restrictions"):
        blocking_reasons.append("manual_go_with_restrictions_requires_restrictions")

    return {
        "status": "valid" if not blocking_reasons else "invalid",
        "decision": decision if decision in VALID_DECISIONS else None,
        "blocking_reasons": sorted(set(blocking_reasons)),
    }


def collect_policy_violations(payloads: Mapping[str, Mapping[str, Any]]) -> list[str]:
    violations: list[str] = []
    for name, payload in payloads.items():
        for key, value in iter_key_values(payload):
            if key in PROHIBITED_TRUE_KEYS and is_truthy(value):
                violations.append(f"{name}:{key}=true")
    return sorted(set(violations))


def decision_template() -> dict[str, Any]:
    return {
        "decision": "GO | NO_GO | GO_WITH_RESTRICTIONS | DEFER",
        "decided_at": "2026-01-01T00:00:00Z",
        "decider": "human-operator-name",
        "evidence_pack_id": "evidence-pack-id",
        "rationale": "human-readable rationale",
        "restrictions": [],
        "acknowledges_risk": True,
        "acknowledges_no_automatic_release": True,
    }


def summarize_decision(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    restrictions = payload.get("restrictions")
    return {
        "decision": normalize_decision(payload.get("decision")),
        "decided_at": payload.get("decided_at"),
        "decider": payload.get("decider"),
        "evidence_pack_id": payload.get("evidence_pack_id"),
        "restrictions_count": len(restrictions) if isinstance(restrictions, list) else 0,
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


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_decision(value: Any) -> str:
    return normalize(value).upper().replace("-", "_").replace(" ", "_")


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def iter_key_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key), nested
            if isinstance(nested, (Mapping, list, tuple)):
                yield from iter_key_values(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_key_values(item)
