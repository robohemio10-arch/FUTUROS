"""Canonical contracts for the Daily Paper/Master Learning Loop.

The module is pure and deterministic. It defines schemas, source identifiers and
paper/shadow safety boundaries for future branches without reading any runtime
source or writing any artifact.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


DAILY_LEARNING_SCHEMA_VERSION = "daily_learning_contracts_v1"
SOURCE_MAP_SCHEMA_VERSION = "daily_learning_source_map_v1"

SAFETY_FLAGS: dict[str, bool] = {
    "research_only": True,
    "paper_only": True,
    "shadow_only": True,
    "read_only": True,
    "operational_authority": False,
    "can_apply_to_freqtrade": False,
    "can_apply_to_risk_manager": False,
    "can_promote_rules": False,
    "can_promote_model": False,
    "live_trading_enabled": False,
    "canary_release_allowed": False,
    "live_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
    "updates_freqtrade": False,
    "updates_risk_manager": False,
    "updates_qlib_runtime": False,
    "updates_ai_shadow_runtime": False,
    "writes_runtime": False,
    "writes_data": False,
    "writes_sqlite": False,
    "writes_parquet": False,
    "runs_training": False,
    "runs_ocr": False,
    "runs_ai_shadow_incremental": False,
}

REQUIRED_SOURCE_IDS = (
    "freqtrade_paper_trades_db",
    "legacy_trade_dataset_xlsx",
    "btc_15s_candles",
    "eth_15s_candles",
    "paper_master_divergence_research_closeout",
)

OPTIONAL_SOURCE_IDS = (
    "ai_shadow_decision_logger_report",
    "ai_shadow_outcome_tracker_report",
    "ai_selector_observations",
    "market_data_health_audit_report",
    "runtime_evidence_pack",
    "readiness_snapshot",
    "paper_shadow_soak_gap_accounting",
)

REQUIRED_FUTURE_BRANCHES = [
    "codex/daily-learning-readonly-loaders-v1",
    "codex/daily-paper-master-kpi-pack-v1",
    "codex/daily-paper-master-divergence-and-alignment-v1",
    "codex/daily-candle-coverage-and-entry-features-v1",
    "codex/daily-mistake-and-winner-catalog-v1",
    "codex/daily-pattern-mining-research-v1",
    "codex/daily-candidate-shadow-rule-registry-v1",
    "codex/daily-shadow-rule-oos-validation-v1",
    "codex/daily-learning-ai-shadow-feedback-bridge-v1",
    "codex/daily-learning-qlib-research-dataset-v1",
    "codex/daily-paper-master-learning-loop-orchestrator-v1",
    "codex/daily-learning-scheduler-paper-v1",
    "codex/dashboard-daily-learning-command-center-v1",
    "codex/daily-learning-evidence-readiness-integration-v1",
    "codex/daily-learning-loop-closeout-handover-v1",
]

ALLOWED_NEXT_STEPS = [
    "implementar loaders read-only em branch futura",
    "materializar KPI pack diario em branch futura",
    "comparar Paper vs dataset legado com fontes carregadas em branch futura",
    "validar coverage de candles e entry features em branch futura",
    "catalogar mistakes e winners em research-only",
    "minerar padroes somente como pesquisa",
    "registrar regras candidatas sem promocao",
    "validar candidatos fora da amostra antes de qualquer discussao operacional",
]

FORBIDDEN_ACTIONS = [
    "ler fontes reais nesta branch",
    "calcular KPIs nesta branch",
    "alterar Freqtrade",
    "alterar RiskManager",
    "alterar Qlib runtime",
    "alterar IA Shadow runtime",
    "alterar modelos",
    "alterar datasets",
    "criar scheduler",
    "criar dashboard",
    "habilitar live",
    "habilitar canary",
    "enviar ordem real",
    "usar exchange privada",
    "escrever artefatos em data/runtime/reports/logs/freqtrade",
]


def build_daily_learning_source_map(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the canonical source map without touching the filesystem."""
    source_map: dict[str, Any] = {
        "schema_version": SOURCE_MAP_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "source_map_defined_without_runtime_readers",
        "project_root": _project_root_text(project_root),
        **SAFETY_FLAGS,
        "required_source_ids": list(REQUIRED_SOURCE_IDS),
        "optional_source_ids": list(OPTIONAL_SOURCE_IDS),
        "sources": [
            _source(
                "freqtrade_paper_trades_db",
                "paper_execution",
                "freqtrade/user_data/tradesv3.dryrun.sqlite",
                True,
                "daily",
                True,
                "Paper execution DB path for future read-only loader contracts.",
            ),
            _source(
                "legacy_trade_dataset_xlsx",
                "master_reference",
                "data/processed/legacy_trade_dataset.xlsx",
                True,
                "daily_or_on_new_ocr_batch",
                True,
                "Canonical master reference; this branch does not open it.",
            ),
            _source(
                "btc_15s_candles",
                "market_data",
                "data/raw/binance_futures_klines_15s/BTCUSDT",
                True,
                "daily",
                True,
                "BTCUSDT 15s candle source for future coverage analysis.",
            ),
            _source(
                "eth_15s_candles",
                "market_data",
                "data/raw/binance_futures_klines_15s/ETHUSDT",
                True,
                "daily",
                True,
                "ETHUSDT 15s candle source for future coverage analysis.",
            ),
            _source(
                "paper_master_divergence_research_closeout",
                "research_closeout",
                "docs/PAPER_MASTER_DIVERGENCE_RESEARCH_CLOSEOUT_V1.md",
                True,
                "versioned_static",
                False,
                "Versioned Branch 01 decision; no runtime loader required.",
            ),
            _source(
                "ai_shadow_decision_logger_report",
                "ai_shadow",
                "data/reports/ai_shadow_decision_logger_report.json",
                False,
                "daily",
                True,
                "Optional AI Shadow decision evidence for future bridge work.",
            ),
            _source(
                "ai_shadow_outcome_tracker_report",
                "ai_shadow",
                "data/reports/ai_shadow_outcome_tracker_report.json",
                False,
                "daily",
                True,
                "Optional AI Shadow outcome evidence for future bridge work.",
            ),
            _source(
                "ai_selector_observations",
                "ai_selector",
                "data/reports/freqtrade_paper_ai_selector_observations.jsonl",
                False,
                "daily",
                True,
                "Optional paper selector observations for future read-only loaders.",
            ),
            _source(
                "market_data_health_audit_report",
                "market_health",
                "data/reports/market_data_health_audit_report.json",
                False,
                "daily",
                True,
                "Optional market health context; not readiness authority here.",
            ),
            _source(
                "runtime_evidence_pack",
                "runtime_evidence",
                "data/reports/runtime_evidence_pack_v2.json",
                False,
                "daily",
                True,
                "Optional runtime evidence pack for future integration.",
            ),
            _source(
                "readiness_snapshot",
                "readiness",
                "data/reports/readiness_snapshot_v2.json",
                False,
                "daily",
                True,
                "Optional readiness context; this contract cannot release live.",
            ),
            _source(
                "paper_shadow_soak_gap_accounting",
                "soak_gap_accounting",
                "data/reports/paper_shadow_soak_gap_accounting_report.json",
                False,
                "daily",
                True,
                "Optional soak gap context; this contract cannot release canary.",
            ),
        ],
    }
    source_map["validation_errors"] = validate_daily_learning_source_map(source_map)
    return source_map


def build_daily_learning_contract_payload(
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the canonical daily learning contract payload."""
    payload: dict[str, Any] = {
        "schema_version": DAILY_LEARNING_SCHEMA_VERSION,
        "source_map_schema_version": SOURCE_MAP_SCHEMA_VERSION,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": "contracts_defined_without_runtime_readers",
        "project_root": _project_root_text(project_root),
        **SAFETY_FLAGS,
        "source_map": build_daily_learning_source_map(project_root),
        "daily_learning_scope": {
            "defines_contracts": True,
            "defines_source_map": True,
            "loads_sources": False,
            "computes_kpis": False,
            "writes_reports": False,
            "updates_models": False,
            "updates_risk": False,
            "updates_execution": False,
        },
        "required_future_branches": list(REQUIRED_FUTURE_BRANCHES),
        "allowed_next_steps": list(ALLOWED_NEXT_STEPS),
        "forbidden_actions": list(FORBIDDEN_ACTIONS),
        "readiness_policy": {
            "source_map_is_not_readiness_evidence": True,
            "daily_learning_outputs_do_not_release_live": True,
            "daily_learning_outputs_do_not_release_canary": True,
            "manual_go_no_go_required": True,
            "thirty_day_gap_free_soak_required_for_future_canary_review": True,
        },
        "operator_decision": {
            "live_release_allowed": False,
            "canary_release_allowed": False,
            "freqtrade_strategy_change_allowed": False,
            "risk_manager_change_allowed": False,
            "model_promotion_allowed": False,
            "shadow_rule_promotion_allowed": False,
            "final_decision": "BLOQUEADO_PARA_OPERACAO_MANTER_EM_RESEARCH",
        },
    }
    payload["validation_errors"] = validate_daily_learning_contract_payload(payload)
    return payload


def validate_daily_learning_source_map(payload: Mapping[str, Any]) -> list[str]:
    """Validate the source map contract without reading any source."""
    errors: list[str] = []
    _validate_common_header(
        payload,
        errors,
        schema_version=SOURCE_MAP_SCHEMA_VERSION,
        reason="source_map_defined_without_runtime_readers",
    )
    sources = payload.get("sources")
    if not isinstance(sources, list):
        errors.append("sources_must_be_list")
        sources = []
    source_ids: list[str] = []
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            errors.append(f"source_{index}_must_be_object")
            continue
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"source_{index}_missing_source_id")
            continue
        source_ids.append(source_id)
        for key in (
            "category",
            "expected_path",
            "required",
            "freshness_policy",
            "current_branch_reads_source",
            "current_branch_writes_source",
            "loader_allowed_future_branch",
            "notes",
        ):
            if key not in source:
                errors.append(f"{source_id}_missing_{key}")
        if source.get("current_branch_reads_source") is not False:
            errors.append(f"{source_id}_current_branch_reads_source_must_be_false")
        if source.get("current_branch_writes_source") is not False:
            errors.append(f"{source_id}_current_branch_writes_source_must_be_false")
    if len(source_ids) != len(set(source_ids)):
        errors.append("source_ids_must_be_unique")
    for source_id in REQUIRED_SOURCE_IDS:
        if source_id not in source_ids:
            errors.append(f"missing_required_source:{source_id}")
    for source_id in OPTIONAL_SOURCE_IDS:
        if source_id not in source_ids:
            errors.append(f"missing_optional_source:{source_id}")
    return errors


def validate_daily_learning_contract_payload(
    payload: Mapping[str, Any],
) -> list[str]:
    """Validate the full contract payload."""
    errors: list[str] = []
    _validate_common_header(
        payload,
        errors,
        schema_version=DAILY_LEARNING_SCHEMA_VERSION,
        reason="contracts_defined_without_runtime_readers",
    )
    if payload.get("source_map_schema_version") != SOURCE_MAP_SCHEMA_VERSION:
        errors.append("source_map_schema_version_mismatch")
    source_map = payload.get("source_map")
    if not isinstance(source_map, Mapping):
        errors.append("source_map_must_be_object")
    else:
        errors.extend(
            f"source_map:{error}"
            for error in validate_daily_learning_source_map(source_map)
        )
    scope = _mapping(payload.get("daily_learning_scope"))
    expected_scope = {
        "defines_contracts": True,
        "defines_source_map": True,
        "loads_sources": False,
        "computes_kpis": False,
        "writes_reports": False,
        "updates_models": False,
        "updates_risk": False,
        "updates_execution": False,
    }
    for key, expected in expected_scope.items():
        if scope.get(key) is not expected:
            errors.append(f"daily_learning_scope_{key}_mismatch")
    readiness = _mapping(payload.get("readiness_policy"))
    for key in (
        "source_map_is_not_readiness_evidence",
        "daily_learning_outputs_do_not_release_live",
        "daily_learning_outputs_do_not_release_canary",
        "manual_go_no_go_required",
        "thirty_day_gap_free_soak_required_for_future_canary_review",
    ):
        if readiness.get(key) is not True:
            errors.append(f"readiness_policy_{key}_must_be_true")
    future = payload.get("required_future_branches")
    if future != REQUIRED_FUTURE_BRANCHES:
        errors.append("required_future_branches_mismatch")
    return errors


def _source(
    source_id: str,
    category: str,
    expected_path: str,
    required: bool,
    freshness_policy: str,
    loader_allowed_future_branch: bool,
    notes: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "category": category,
        "expected_path": expected_path,
        "required": required,
        "freshness_policy": freshness_policy,
        "current_branch_reads_source": False,
        "current_branch_writes_source": False,
        "loader_allowed_future_branch": loader_allowed_future_branch,
        "notes": notes,
    }


def _validate_common_header(
    payload: Mapping[str, Any],
    errors: list[str],
    *,
    schema_version: str,
    reason: str,
) -> None:
    expected_header: dict[str, Any] = {
        "schema_version": schema_version,
        "project_name": "SMART FUTUROS",
        "status": "blocked",
        "decision": "MANTER_EM_RESEARCH",
        "reason": reason,
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            errors.append(f"{key}_mismatch")
    for key, expected in SAFETY_FLAGS.items():
        if payload.get(key) is not expected:
            errors.append(f"{key}_must_be_{str(expected).lower()}")


def _project_root_text(project_root: str | Path | None) -> str | None:
    if project_root is None:
        return None
    return str(Path(project_root))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
