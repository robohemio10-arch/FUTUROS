from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA_VERSION = "canonical_economic_evidence_plane_v1"
ALLOWED_STAGE_STATUSES = {"ok", "waiting", "blocked"}
STAGE_FAILURES = (OSError, ValueError, RuntimeError, TypeError, KeyError, ImportError)

SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "research_only": True,
    "operational_authority": False,
    "live_trading_enabled": False,
    "live_release_allowed": False,
    "canary_release_allowed": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
}

STAGE_FILES = {
    "branch08": "branch08_economic_benchmark_v1.json",
    "branch09": "branch09_ai_shadow_economic_challenger_v1.json",
    "branch10": "branch10_market_intelligence_pnl_ablation_v1.json",
    "branch11": "branch11_execution_intelligence_net_pnl_attribution_v1.json",
    "branch12": "branch12_opportunity_allocator_capital_hour_uplift_v1.json",
    "branch13": "branch13_component_economic_attribution_ledger_v1.json",
    "branch14": "branch14_economic_control_treatment_scorecard_v1.json",
    "branch15": "branch15_portfolio_of_alphas_oos_economic_selection_v1.json",
    "branch16": "branch16_research_council_alpha_uplift_ab_v1.json",
}
SUMMARY_FILE = "canonical_economic_evidence_plane_v1.json"

DATASET_FILENAME = "official_trades_master_qlib_dataset_v1.parquet"
FEATURE_CONTRACT_FILENAME = "official_trades_master_qlib_feature_contract_v1.json"
DATASET_MANIFEST_FILENAME = "official_trades_master_qlib_dataset_manifest_v1.json"
SPLIT_MANIFEST_FILENAME = "official_trades_master_qlib_split_manifest_v1.json"
SLEEVE_EVIDENCE_FILENAME = "portfolio_of_alphas_oos_sleeve_evidence_v1.json"
COUNCIL_EVIDENCE_FILENAME = "research_council_alpha_uplift_ab_evidence_v1.json"


@dataclass(frozen=True)
class PlaneConfig:
    project_root: Path
    data_root: Path
    output_dir: Path
    master_path: Path | None = None
    paper_csv_path: Path | None = None
    paper_snapshot_sqlite_path: Path | None = None
    dataset_path: Path | None = None
    feature_contract_path: Path | None = None
    dataset_manifest_path: Path | None = None
    split_manifest_path: Path | None = None
    shadow_evidence_path: Path | None = None
    sleeve_evidence_path: Path | None = None
    council_evidence_path: Path | None = None
    treatment_monitor_path: Path | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _blocked(stage_id: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": f"{stage_id}_economic_plane_controlled_failure_v1",
        "status": "blocked",
        "reason": reason,
        "decision": "OBSERVE_ONLY_RESEARCH_EVIDENCE",
        **SAFETY_FLAGS,
    }


def _validate_stage_report(stage_id: str, report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        return _blocked(stage_id, "stage_report_not_object")
    status = report.get("status")
    if status not in ALLOWED_STAGE_STATUSES:
        return _blocked(stage_id, f"stage_status_invalid:{status}")
    return dict(report)


def _run_stage(stage_id: str, runner: Callable[[], object]) -> dict[str, Any]:
    try:
        report = runner()
    except STAGE_FAILURES as exc:
        return _blocked(stage_id, f"stage_failed:{type(exc).__name__}:{exc}")
    return _validate_stage_report(stage_id, report)


def _resolve_explicit(path: Path | None, root: Path) -> Path | None:
    if path is None:
        return None
    candidate = path if path.is_absolute() else root / path
    return candidate.resolve()


def _resolve_required(
    *,
    explicit: Path | None,
    data_root: Path,
    preferred_relatives: tuple[str, ...],
    filename: str,
) -> tuple[Path | None, str | None]:
    candidate = _resolve_explicit(explicit, data_root)
    if candidate is not None:
        if candidate.is_file():
            return candidate, None
        return None, f"explicit_source_missing:{candidate}"

    for relative in preferred_relatives:
        preferred = (data_root / relative).resolve()
        if preferred.is_file():
            return preferred, None

    matches = sorted(
        {path.resolve() for path in data_root.rglob(filename) if path.is_file()},
        key=str,
    )
    if not matches:
        return None, f"source_not_found:{filename}"
    if len(matches) != 1:
        return None, f"source_ambiguous:{filename}:count={len(matches)}"
    return matches[0], None


def _resolve_optional(
    *,
    explicit: Path | None,
    data_root: Path,
    filename: str,
) -> tuple[Path | None, str | None]:
    candidate = _resolve_explicit(explicit, data_root)
    if candidate is not None:
        if candidate.is_file():
            return candidate, None
        return None, f"explicit_optional_source_missing:{candidate}"

    matches = sorted(
        {path.resolve() for path in data_root.rglob(filename) if path.is_file()},
        key=str,
    )
    if not matches:
        return None, None
    if len(matches) != 1:
        return None, f"optional_source_ambiguous:{filename}:count={len(matches)}"
    return matches[0], None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid_json:{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        default=str,
    ) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _load_default_builders() -> dict[str, Callable[..., dict[str, Any]]]:
    from smartcrypto.research.aibot_parity import (
        component_economic_attribution_ledger as branch13,
        economic_control_treatment_scorecard as branch14,
        execution_intelligence_net_pnl_attribution as branch11,
        market_intelligence_pnl_ablation as branch10,
        opportunity_allocator_capital_hour_uplift as branch12,
    )
    from smartcrypto.research.aibot_parity.ai_shadow_economic_challenger import (
        build_ai_shadow_economic_challenger_sync_v1,
    )
    from smartcrypto.research.aibot_parity.economic_benchmark import (
        build_economic_benchmark,
    )
    from smartcrypto.research.portfolio_of_alphas.oos_economic_selection import (
        build_portfolio_of_alphas_oos_economic_selection_from_scorecard,
    )
    from smartcrypto.research.research_council.alpha_uplift_ab import (
        build_research_council_alpha_uplift_ab_from_scorecard,
    )

    return {
        "branch08": build_economic_benchmark,
        "branch09": build_ai_shadow_economic_challenger_sync_v1,
        "branch10": branch10.build_market_intelligence_pnl_ablation_v1,
        "branch11": branch11.build_execution_intelligence_net_pnl_attribution_v1,
        "branch12": branch12.build_opportunity_allocator_capital_hour_uplift_v1,
        "branch13": branch13.build_component_economic_attribution_ledger_from_reports,
        "branch14": branch14.build_economic_control_treatment_scorecard_from_reports,
        "branch15": build_portfolio_of_alphas_oos_economic_selection_from_scorecard,
        "branch16": build_research_council_alpha_uplift_ab_from_scorecard,
    }


def _source_descriptor(path: Path | None, issue: str | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "exists": False, "issue": issue}
    try:
        metadata = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": metadata.st_size,
            "modified_at_utc": _iso_utc(
                datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
            ),
            "issue": issue,
        }
    except OSError as exc:
        return {
            "path": str(path),
            "exists": False,
            "issue": f"source_stat_failed:{type(exc).__name__}",
        }


def _treatment_overlay(path: Path | None, issue: str | None, now: datetime) -> dict[str, Any]:
    if issue is not None:
        return {
            "status": "blocked",
            "reason": issue,
            "path": str(path) if path is not None else None,
        }
    if path is None or not path.is_file():
        return {
            "status": "waiting",
            "reason": "canonical_treatment_monitor_not_materialized",
            "path": str(path) if path is not None else None,
        }
    try:
        report = _read_json(path)
        started = datetime.fromisoformat(
            str(report.get("experiment_started_at_utc")).replace("Z", "+00:00")
        )
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        observed_days = max(
            0.0,
            (now - started.astimezone(UTC)).total_seconds() / 86400.0,
        )
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        return {
            "status": "blocked",
            "reason": f"canonical_treatment_monitor_invalid:{type(exc).__name__}",
            "path": str(path),
        }

    scorer = report.get("scorer")
    sample = report.get("sample")
    paired = report.get("paired_uplift")
    if not isinstance(scorer, dict) or not isinstance(sample, dict) or not isinstance(paired, dict):
        return {
            "status": "blocked",
            "reason": "canonical_treatment_monitor_schema_incomplete",
            "path": str(path),
        }

    return {
        "status": "ok",
        "reason": "canonical_treatment_runtime_evidence_observed",
        "path": str(path),
        "sha256": _sha256(path),
        "experiment_started_at_utc": report.get("experiment_started_at_utc"),
        "observed_days": observed_days,
        "candidate_decision_count": scorer.get("candidate_decision_count"),
        "scored_decision_count": scorer.get("scored_decision_count"),
        "selected_decision_count": scorer.get("selected_decision_count"),
        "scorer_coverage": scorer.get("coverage"),
        "control_linked_trade_count": sample.get("control_linked_trade_count"),
        "treatment_linked_trade_count": sample.get("treatment_linked_trade_count"),
        "financially_resolved_decisions": sample.get("financially_resolved_decisions"),
        "selected_closed_trades": sample.get("selected_closed_trades"),
        "paired_delta_net_pnl_total": paired.get("delta_net_pnl_total"),
        "paired_delta_net_pnl_mean": paired.get("delta_net_pnl_mean"),
        "branch14_frozen_semantics_mutated": False,
        "note": (
            "Runtime Paper B evidence is surfaced as an overlay only; "
            "it is not injected into the frozen Branch 14 scorecard semantics."
        ),
    }


def run_economic_evidence_cycle(
    config: PlaneConfig,
    *,
    write_reports: bool,
    builders: Mapping[str, Callable[..., dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now or _utc_now()).astimezone(UTC)
    root = config.project_root.resolve()
    data_root = config.data_root.resolve()
    output_dir = config.output_dir.resolve()
    active_builders = dict(builders or _load_default_builders())

    master, master_issue = _resolve_required(
        explicit=config.master_path,
        data_root=data_root,
        preferred_relatives=("trades/trades_master.xlsx",),
        filename="trades_master.xlsx",
    )
    paper_csv, paper_issue = _resolve_required(
        explicit=config.paper_csv_path,
        data_root=data_root,
        preferred_relatives=("trades/inbox/freqtrade_paper_closed_trades.csv",),
        filename="freqtrade_paper_closed_trades.csv",
    )
    paper_sqlite, sqlite_issue = _resolve_required(
        explicit=config.paper_snapshot_sqlite_path,
        data_root=data_root,
        preferred_relatives=("snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite",),
        filename="tradesv3.paper.snapshot.sqlite",
    )
    dataset, dataset_issue = _resolve_required(
        explicit=config.dataset_path,
        data_root=data_root,
        preferred_relatives=(),
        filename=DATASET_FILENAME,
    )
    feature_contract, feature_contract_issue = _resolve_required(
        explicit=config.feature_contract_path,
        data_root=data_root,
        preferred_relatives=(),
        filename=FEATURE_CONTRACT_FILENAME,
    )
    dataset_manifest, dataset_manifest_issue = _resolve_required(
        explicit=config.dataset_manifest_path,
        data_root=data_root,
        preferred_relatives=(),
        filename=DATASET_MANIFEST_FILENAME,
    )
    split_manifest, split_manifest_issue = _resolve_required(
        explicit=config.split_manifest_path,
        data_root=data_root,
        preferred_relatives=(),
        filename=SPLIT_MANIFEST_FILENAME,
    )
    sleeve_evidence, sleeve_issue = _resolve_optional(
        explicit=config.sleeve_evidence_path,
        data_root=data_root,
        filename=SLEEVE_EVIDENCE_FILENAME,
    )
    council_evidence, council_issue = _resolve_optional(
        explicit=config.council_evidence_path,
        data_root=data_root,
        filename=COUNCIL_EVIDENCE_FILENAME,
    )

    treatment_path = _resolve_explicit(config.treatment_monitor_path, data_root)
    if treatment_path is None:
        treatment_path = (
            data_root / "reports/canonical_treatment/economic_monitor_v1.json"
        ).resolve()
    treatment_issue = None

    reports: dict[str, dict[str, Any]] = {}

    if master is None or paper_csv is None or paper_sqlite is None:
        reports["branch08"] = _blocked(
            "branch08",
            ";".join(
                value
                for value in (master_issue, paper_issue, sqlite_issue)
                if value is not None
            ),
        )
    else:
        reports["branch08"] = _run_stage(
            "branch08",
            lambda: active_builders["branch08"](
                master_path=master,
                paper_path=paper_csv,
                sqlite_path=paper_sqlite,
            ),
        )

    reports["branch09"] = _run_stage(
        "branch09",
        lambda: active_builders["branch09"](
            project_root=data_root.parent,
            evidence_path=(
                _resolve_explicit(config.shadow_evidence_path, data_root)
                if config.shadow_evidence_path is not None
                else None
            ),
        ),
    )

    branch10_issues = (
        dataset_issue,
        feature_contract_issue,
        dataset_manifest_issue,
        split_manifest_issue,
    )
    if any(branch10_issues):
        reports["branch10"] = _blocked(
            "branch10",
            ";".join(value for value in branch10_issues if value is not None),
        )
    else:
        assert dataset is not None
        assert feature_contract is not None
        assert dataset_manifest is not None
        assert split_manifest is not None
        reports["branch10"] = _run_stage(
            "branch10",
            lambda: active_builders["branch10"](
                project_root=root,
                dataset_path=dataset,
                feature_contract_path=feature_contract,
                dataset_manifest_path=dataset_manifest,
                split_manifest_path=split_manifest,
            ),
        )

    if master is None:
        reports["branch11"] = _blocked("branch11", master_issue or "master_missing")
        reports["branch12"] = _blocked("branch12", master_issue or "master_missing")
    else:
        reports["branch11"] = _run_stage(
            "branch11",
            lambda: active_builders["branch11"](master_path=master),
        )
        reports["branch12"] = _run_stage(
            "branch12",
            lambda: active_builders["branch12"](master_path=master),
        )

    reports["branch13"] = _run_stage(
        "branch13",
        lambda: active_builders["branch13"](
            branch10_report=reports["branch10"],
            branch11_report=reports["branch11"],
            branch12_report=reports["branch12"],
        ),
    )
    reports["branch14"] = _run_stage(
        "branch14",
        lambda: active_builders["branch14"](
            branch09_report=reports["branch09"],
            branch10_report=reports["branch10"],
            branch11_report=reports["branch11"],
            branch12_report=reports["branch12"],
            ledger_report=reports["branch13"],
        ),
    )

    if sleeve_issue is not None:
        reports["branch15"] = _blocked("branch15", sleeve_issue)
    else:
        sleeve_payload = _read_json(sleeve_evidence) if sleeve_evidence is not None else None
        reports["branch15"] = _run_stage(
            "branch15",
            lambda: active_builders["branch15"](
                scorecard_report=reports["branch14"],
                sleeve_evidence=sleeve_payload,
            ),
        )

    if council_issue is not None:
        reports["branch16"] = _blocked("branch16", council_issue)
    else:
        council_payload = _read_json(council_evidence) if council_evidence is not None else None
        reports["branch16"] = _run_stage(
            "branch16",
            lambda: active_builders["branch16"](
                scorecard_report=reports["branch14"],
                council_ab_evidence=council_payload,
            ),
        )

    treatment = _treatment_overlay(treatment_path, treatment_issue, generated_at)

    counts = {
        status: sum(report.get("status") == status for report in reports.values())
        for status in sorted(ALLOWED_STAGE_STATUSES)
    }
    stage_index = {
        stage_id: {
            "status": report.get("status"),
            "reason": report.get("reason"),
            "decision": report.get("decision"),
            "report_path": str(output_dir / STAGE_FILES[stage_id]),
        }
        for stage_id, report in reports.items()
    }

    sources = {
        "master": _source_descriptor(master, master_issue),
        "paper_csv": _source_descriptor(paper_csv, paper_issue),
        "paper_snapshot_sqlite": _source_descriptor(paper_sqlite, sqlite_issue),
        "dataset": _source_descriptor(dataset, dataset_issue),
        "feature_contract": _source_descriptor(feature_contract, feature_contract_issue),
        "dataset_manifest": _source_descriptor(dataset_manifest, dataset_manifest_issue),
        "split_manifest": _source_descriptor(split_manifest, split_manifest_issue),
        "sleeve_evidence": _source_descriptor(sleeve_evidence, sleeve_issue),
        "council_evidence": _source_descriptor(council_evidence, council_issue),
    }

    summary_core: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason": "canonical_economic_evidence_cycle_complete",
        "decision": "OBSERVE_ONLY_RESEARCH_EVIDENCE",
        "generated_at_utc": _iso_utc(generated_at),
        "stage_count": len(reports),
        "stage_status_counts": counts,
        "stages": stage_index,
        "sources": sources,
        "canonical_treatment_runtime_overlay": treatment,
        "branch17_forward_observation": {
            "certification_attempted": False,
            "final_gate_unchanged": True,
            "observed_days": treatment.get("observed_days"),
            "financially_resolved_decisions": treatment.get(
                "financially_resolved_decisions"
            ),
            "selected_closed_trades": treatment.get("selected_closed_trades"),
            "scorer_coverage": treatment.get("scorer_coverage"),
            "paired_delta_net_pnl_total": treatment.get(
                "paired_delta_net_pnl_total"
            ),
            "reason": "interim_metrics_only_no_final_certification",
        },
        "write_requested": write_reports,
        "write_performed": False,
        **SAFETY_FLAGS,
        "safety_flags": dict(SAFETY_FLAGS),
    }
    summary_core["cycle_evidence_sha256"] = _canonical_hash(summary_core)

    if write_reports:
        for stage_id, report in reports.items():
            _write_json_atomic(output_dir / STAGE_FILES[stage_id], report)
        summary_core["write_performed"] = True
        summary_core["cycle_evidence_sha256"] = _canonical_hash(
            {key: value for key, value in summary_core.items() if key != "cycle_evidence_sha256"}
        )
        _write_json_atomic(output_dir / SUMMARY_FILE, summary_core)

    return summary_core
