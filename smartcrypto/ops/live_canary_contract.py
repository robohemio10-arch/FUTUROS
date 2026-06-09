from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "live_canary_contract_with_hard_blocks_v1"
DEFAULT_OUTPUT_PATH = Path("data/reports/live_canary_contract_with_hard_blocks.json")
DEFAULT_MANUAL_GOVERNANCE_PATH = Path("data/reports/manual_go_no_go_live_canary_governance.json")

CANONICAL_ALLOWED_SYMBOLS = ("BTC/USDT", "ETH/USDT")
CANONICAL_ALLOWED_SYMBOL_ALIASES = {
    "BTC/USDT",
    "ETH/USDT",
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "BTCUSDT",
    "ETHUSDT",
}
CANONICAL_GLOBAL_CAP_MIN_USDT = 20.0
CANONICAL_GLOBAL_CAP_MAX_USDT = 50.0
CANONICAL_PER_SYMBOL_CAP_USDT = 10.0
CANONICAL_MAX_SAFETY_ORDERS = 0
CANONICAL_MARTINGALE_MULTIPLIER = 1.0
CANONICAL_PREFERRED_ORDER_TYPE = "LIMIT_MAKER"

REQUIRED_TRUE_KEYS = (
    "manual_go_no_go_required",
    "hard_blocks_enforced",
    "kill_switch_required",
    "reconciliation_required",
    "rollback_required",
    "observability_required",
    "paper_shadow_evidence_required",
)
REQUIRED_FALSE_KEYS = (
    "auto_promotion_allowed",
    "market_buy_allowed",
    "market_order_allowed",
    "martingale_allowed",
    "safety_orders_allowed",
    "unbounded_capital_allowed",
    "private_exchange_access_allowed",
    "order_submission_allowed",
    "live_release_allowed",
    "canary_release_allowed",
    "changes_risk",
    "changes_training_dataset",
    "writes_trades_master",
    "changes_model",
    "promotes_model",
    "sends_orders",
)
PROHIBITED_TRUE_KEYS = set(REQUIRED_FALSE_KEYS) | {
    "release_allowed",
    "live_trading_enabled",
    "exchange_private_access",
    "real_order_submission_enabled",
}
REQUIRED_MANUAL_GOVERNANCE_STATUSES = {"manual_go_recorded"}


@dataclass(frozen=True)
class ContractResult:
    report: dict[str, Any]
    output_path: Path
    write_performed: bool


@dataclass(frozen=True)
class CanaryLimits:
    global_cap_min_usdt: float = CANONICAL_GLOBAL_CAP_MIN_USDT
    global_cap_max_usdt: float = CANONICAL_GLOBAL_CAP_MAX_USDT
    per_symbol_cap_usdt: float = CANONICAL_PER_SYMBOL_CAP_USDT
    allowed_symbols: tuple[str, ...] = CANONICAL_ALLOWED_SYMBOLS
    max_safety_orders: int = CANONICAL_MAX_SAFETY_ORDERS
    martingale_multiplier: float = CANONICAL_MARTINGALE_MULTIPLIER
    preferred_order_type: str = CANONICAL_PREFERRED_ORDER_TYPE


def build_live_canary_contract_with_hard_blocks(
    *,
    project_root: str | Path = ".",
    output: str | Path = DEFAULT_OUTPUT_PATH,
    manual_governance_path: str | Path = DEFAULT_MANUAL_GOVERNANCE_PATH,
    candidate_config_path: str | Path | None = None,
    no_write: bool = False,
    now: datetime | None = None,
) -> ContractResult:
    root = Path(project_root).resolve()
    output_path = resolve_under_root(root, output)
    governance_file = resolve_under_root(root, manual_governance_path)
    current_time = now or datetime.now(timezone.utc)

    blocking_reasons: list[str] = []
    warning_reasons: list[str] = []
    next_required_actions: list[str] = [
        "Executar somente após evidência paper/shadow contínua e decisão humana registrada.",
        "Manter kill switch, reconciliação, observabilidade e rollback operacional antes de qualquer etapa posterior.",
    ]

    governance_payload: Mapping[str, Any] | None = None
    if governance_file.exists():
        try:
            governance_payload = load_json_object(governance_file)
            blocking_reasons.extend(validate_manual_governance(governance_payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            blocking_reasons.append(f"manual_governance_invalid: {type(exc).__name__}: {exc}")
    else:
        blocking_reasons.append("manual_governance_missing")

    candidate_payload: Mapping[str, Any] | None = None
    candidate_path_text: str | None = None
    if candidate_config_path is not None:
        candidate_file = resolve_under_root(root, candidate_config_path)
        candidate_path_text = str(candidate_file)
        try:
            candidate_payload = load_json_object(candidate_file)
            blocking_reasons.extend(validate_candidate_config(candidate_payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            blocking_reasons.append(f"candidate_config_invalid: {type(exc).__name__}: {exc}")
    else:
        warning_reasons.append("candidate_config_not_supplied_contract_only")

    contract = build_canonical_contract()
    if blocking_reasons:
        status = "blocked"
    elif warning_reasons:
        status = "contract_defined_with_warnings"
    else:
        status = "contract_defined"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(current_time),
        "project_root": str(root),
        "status": status,
        "manual_governance_path": str(governance_file),
        "candidate_config_path": candidate_path_text,
        "manual_governance_summary": summarize_governance(governance_payload),
        "candidate_config_summary": summarize_candidate(candidate_payload),
        "contract": contract,
        "hard_blocks": build_hard_blocks(),
        "blocking_reasons": sorted(set(blocking_reasons)),
        "warning_reasons": sorted(set(warning_reasons)),
        "next_required_actions": sorted(set(next_required_actions)),
        "manual_go_no_go_required": True,
        "hard_blocks_enforced": True,
        "auto_promotion_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "release_allowed": False,
        "changes_risk": False,
        "sends_orders": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "changes_model": False,
        "promotes_model": False,
    }

    write_performed = False
    if not no_write:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_performed = True

    return ContractResult(report=report, output_path=output_path, write_performed=write_performed)


def build_canonical_contract() -> dict[str, Any]:
    limits = CanaryLimits()
    return {
        "global_cap_min_usdt": limits.global_cap_min_usdt,
        "global_cap_max_usdt": limits.global_cap_max_usdt,
        "per_symbol_cap_usdt": limits.per_symbol_cap_usdt,
        "allowed_symbols": list(limits.allowed_symbols),
        "max_safety_orders": limits.max_safety_orders,
        "martingale_multiplier": limits.martingale_multiplier,
        "market_buy_allowed": False,
        "market_order_allowed": False,
        "preferred_order_type": limits.preferred_order_type,
        "kill_switch_required": True,
        "reconciliation_required": True,
        "rollback_required": True,
        "observability_required": True,
        "paper_shadow_evidence_required": True,
    }


def build_hard_blocks() -> dict[str, Any]:
    return {
        "manual_go_no_go_required": True,
        "hard_blocks_enforced": True,
        "auto_promotion_allowed": False,
        "release_allowed": False,
        "live_release_allowed": False,
        "canary_release_allowed": False,
        "private_exchange_access_allowed": False,
        "order_submission_allowed": False,
        "sends_orders": False,
        "changes_risk": False,
        "changes_training_dataset": False,
        "writes_trades_master": False,
        "changes_model": False,
        "promotes_model": False,
        "capital_global_min_usdt": CANONICAL_GLOBAL_CAP_MIN_USDT,
        "capital_global_max_usdt": CANONICAL_GLOBAL_CAP_MAX_USDT,
        "capital_per_symbol_max_usdt": CANONICAL_PER_SYMBOL_CAP_USDT,
        "allowed_symbols": list(CANONICAL_ALLOWED_SYMBOLS),
        "max_safety_orders": CANONICAL_MAX_SAFETY_ORDERS,
        "martingale_multiplier": CANONICAL_MARTINGALE_MULTIPLIER,
        "market_buy_allowed": False,
        "market_order_allowed": False,
        "preferred_order_type": CANONICAL_PREFERRED_ORDER_TYPE,
        "kill_switch_required": True,
        "reconciliation_required": True,
        "rollback_required": True,
        "observability_required": True,
    }


def validate_manual_governance(payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    status = normalize(payload.get("status"))
    manual_decision = normalize(payload.get("manual_decision"))
    if status not in REQUIRED_MANUAL_GOVERNANCE_STATUSES:
        violations.append(f"manual_governance_status_not_approved: {status}")
    if manual_decision != "GO":
        violations.append(f"manual_governance_decision_not_go: {manual_decision}")
    if payload.get("manual_go_no_go_required") is not True:
        violations.append("manual_governance_must_require_manual_go_no_go")
    violations.extend(collect_policy_violations("manual_governance", payload))
    return sorted(set(violations))


def validate_candidate_config(payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    symbols = extract_symbols(payload)
    if not symbols:
        violations.append("candidate_symbols_missing")
    else:
        disallowed = [symbol for symbol in symbols if normalize_symbol(symbol) not in CANONICAL_ALLOWED_SYMBOL_ALIASES]
        if disallowed:
            violations.append("candidate_symbols_not_allowed: " + ",".join(sorted(disallowed)))

    global_cap = find_number(payload, ("global_cap_usdt", "max_global_cap_usdt", "capital_global_usdt", "stake_amount_total"))
    if global_cap is None:
        violations.append("candidate_global_cap_missing")
    else:
        if global_cap < CANONICAL_GLOBAL_CAP_MIN_USDT:
            violations.append("candidate_global_cap_below_minimum")
        if global_cap > CANONICAL_GLOBAL_CAP_MAX_USDT:
            violations.append("candidate_global_cap_above_maximum")

    per_symbol_cap = find_number(payload, ("per_symbol_cap_usdt", "max_per_symbol_cap_usdt", "capital_per_symbol_usdt", "stake_amount"))
    if per_symbol_cap is None:
        violations.append("candidate_per_symbol_cap_missing")
    elif per_symbol_cap > CANONICAL_PER_SYMBOL_CAP_USDT:
        violations.append("candidate_per_symbol_cap_above_maximum")

    max_safety_orders = find_number(payload, ("max_safety_orders", "safety_orders"))
    if max_safety_orders is None:
        violations.append("candidate_max_safety_orders_missing")
    elif int(max_safety_orders) != CANONICAL_MAX_SAFETY_ORDERS:
        violations.append("candidate_max_safety_orders_must_be_zero")

    martingale_multiplier = find_number(payload, ("martingale_multiplier", "volume_scale", "safety_order_volume_scale"))
    if martingale_multiplier is None:
        violations.append("candidate_martingale_multiplier_missing")
    elif float(martingale_multiplier) != CANONICAL_MARTINGALE_MULTIPLIER:
        violations.append("candidate_martingale_multiplier_must_be_one")

    order_type = find_string(payload, ("order_type", "entry_order_type", "preferred_order_type"))
    if order_type and normalize(order_type) != CANONICAL_PREFERRED_ORDER_TYPE:
        violations.append("candidate_preferred_order_type_not_limit_maker")

    for required_key in REQUIRED_TRUE_KEYS:
        value = find_value(payload, required_key)
        if value is not None and value is not True:
            violations.append(f"candidate_{required_key}_must_be_true")

    for required_key in REQUIRED_FALSE_KEYS:
        value = find_value(payload, required_key)
        if is_truthy(value):
            violations.append(f"candidate_{required_key}_must_be_false")

    violations.extend(collect_policy_violations("candidate_config", payload))
    return sorted(set(violations))


def collect_policy_violations(prefix: str, payload: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    for key, value in iter_key_values(payload):
        if key in PROHIBITED_TRUE_KEYS and is_truthy(value):
            violations.append(f"{prefix}:{key}=true")
    return sorted(set(violations))


def extract_symbols(payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in iter_key_values(payload):
        if key in {"symbols", "pairs", "pair_whitelist", "allowed_symbols"}:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                values.extend(str(item) for item in value)
    return values


def find_number(payload: Mapping[str, Any], keys: Iterable[str]) -> float | None:
    keys_set = set(keys)
    for key, value in iter_key_values(payload):
        if key in keys_set:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def find_string(payload: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    keys_set = set(keys)
    for key, value in iter_key_values(payload):
        if key in keys_set and value is not None:
            return str(value)
    return None


def find_value(payload: Mapping[str, Any], wanted_key: str) -> Any:
    for key, value in iter_key_values(payload):
        if key == wanted_key:
            return value
    return None


def summarize_governance(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "status": payload.get("status"),
        "manual_decision": payload.get("manual_decision"),
        "manual_decision_status": payload.get("manual_decision_status"),
        "manual_go_no_go_required": payload.get("manual_go_no_go_required"),
    }


def summarize_candidate(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "symbols": extract_symbols(payload),
        "global_cap_usdt": find_number(payload, ("global_cap_usdt", "max_global_cap_usdt", "capital_global_usdt", "stake_amount_total")),
        "per_symbol_cap_usdt": find_number(payload, ("per_symbol_cap_usdt", "max_per_symbol_cap_usdt", "capital_per_symbol_usdt", "stake_amount")),
        "max_safety_orders": find_number(payload, ("max_safety_orders", "safety_orders")),
        "martingale_multiplier": find_number(payload, ("martingale_multiplier", "volume_scale", "safety_order_volume_scale")),
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


def normalize_symbol(value: Any) -> str:
    return normalize(value).upper().replace(" ", "")


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
