"""Deterministic no-training feature-family ablation manifest."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Iterable

from smartcrypto.runtime.integrity_traceability_v2.atomic_writer import (
    AtomicWriteError,
    AtomicWritePolicy,
    atomic_write_json,
    resolve_authorized_target,
)

from .contracts import (
    AblationManifest,
    AblationVariant,
    MarketIntelligenceSnapshot,
    canonical_sha256,
)

_FORBIDDEN_PATTERNS = (
    "future_ret*",
    "target*",
    "label*",
    "*pnl*",
    "exit_reason*",
    "exit_price*",
    "close_time*",
    "mfe*",
    "mae*",
    "trade_outcome*",
)


def build_ablation_manifest(
    snapshot: MarketIntelligenceSnapshot,
    *,
    baseline_feature_names: Iterable[str] = (),
) -> AblationManifest:
    baseline = tuple(
        sorted({str(name).strip() for name in baseline_feature_names if str(name).strip()})
    )
    market_features = _feature_names_by_family(snapshot)
    candidate_names = set(baseline)
    for names in market_features.values():
        candidate_names.update(names)
    rejected = tuple(sorted(name for name in candidate_names if _is_leakage_name(name)))
    if rejected:
        status = "BLOCKED_LEAKAGE"
        reason = "forbidden_or_outcome_feature_detected"
        variants: tuple[AblationVariant, ...] = ()
    else:
        variants_list: list[AblationVariant] = [
            _variant("BASELINE", (), baseline),
        ]
        for family in (
            "flow",
            "spread",
            "basis_funding",
            "open_interest",
            "liquidations",
        ):
            names = market_features.get(family, ())
            if names:
                variants_list.append(
                    _variant(
                        f"BASELINE_PLUS_{family.upper()}",
                        (family,),
                        tuple(sorted(set(baseline) | set(names))),
                    )
                )
        available_families = tuple(
            family for family, names in market_features.items() if names
        )
        if available_families:
            combined = tuple(
                sorted(
                    set(baseline).union(
                        *(set(market_features[family]) for family in available_families)
                    )
                )
            )
            variants_list.append(
                _variant(
                    "BASELINE_PLUS_ALL_AVAILABLE_MARKET_INTELLIGENCE",
                    available_families,
                    combined,
                )
            )
            status = "ABLATION_DATA_READY"
            reason = "feature_family_variants_ready_no_training_performed"
        else:
            status = "NO_AVAILABLE_FEATURES"
            reason = "no_available_market_intelligence_features"
        variants = tuple(variants_list)
    semantic = {
        "snapshot_id": snapshot.snapshot_id,
        "baseline_feature_names": baseline,
        "variants": [item.model_dump(mode="json") for item in variants],
        "rejected_leakage_features": rejected,
        "status": status,
    }
    return AblationManifest(
        ablation_id=f"market-ablation-{canonical_sha256(semantic)}",
        status=status,
        reason=reason,
        snapshot_id=snapshot.snapshot_id,
        baseline_feature_names=baseline,
        variants=variants,
        rejected_leakage_features=rejected,
    )


def _feature_names_by_family(snapshot: MarketIntelligenceSnapshot) -> dict[str, tuple[str, ...]]:
    values = {
        "flow": snapshot.flow_features,
        "spread": snapshot.spread_features,
        "basis_funding": snapshot.basis_funding_features,
        "open_interest": snapshot.open_interest_features,
        "liquidations": snapshot.liquidation_features,
    }
    result: dict[str, tuple[str, ...]] = {}
    for family, payload in values.items():
        health = snapshot.feature_family_statuses.get(family)
        if payload and health is not None and health.status.value in {"FRESH", "STALE"}:
            result[family] = tuple(sorted(payload))
        else:
            result[family] = ()
    return result


def _variant(
    variant_id: str,
    families: tuple[str, ...],
    names: tuple[str, ...],
) -> AblationVariant:
    return AblationVariant(
        variant_id=variant_id,
        feature_families=families,
        feature_names=names,
        feature_count=len(names),
    )


def _is_leakage_name(name: str) -> bool:
    lower = name.casefold()
    return any(fnmatch.fnmatch(lower, pattern) for pattern in _FORBIDDEN_PATTERNS)


class AblationPersistenceError(RuntimeError):
    def __init__(self, reason: str, *, write_performed: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.write_performed = write_performed


def persist_ablation_manifest(
    *,
    project_root: str | Path,
    manifest: AblationManifest,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    report_root = root / "data" / "reports" / "aibot_parity" / "market_intelligence"
    policy = AtomicWritePolicy.restricted((report_root,), working_directory=root)
    try:
        target = (
            report_root / manifest.ablation_id / "ablation_manifest.json"
            if output_json is None
            else resolve_authorized_target(output_json, policy=policy)
        )
    except AtomicWriteError as exc:
        raise AblationPersistenceError(exc.reason) from exc
    written = _write_manifest_once(target, manifest.model_dump(mode="json"), policy=policy)
    return {
        "write_performed": written,
        "output_paths": {"ablation_manifest": target.relative_to(root).as_posix()},
    }


def _write_manifest_once(
    target: Path,
    payload: dict[str, Any],
    *,
    policy: AtomicWritePolicy,
) -> bool:
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise AblationPersistenceError("existing_output_not_regular_file")
        try:
            existing = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AblationPersistenceError("existing_output_unreadable") from exc
        if existing == payload:
            return False
        raise AblationPersistenceError("deterministic_output_conflict")
    try:
        result = atomic_write_json(
            target,
            payload,
            policy=policy,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (AtomicWriteError, OSError, ValueError) as exc:
        raise AblationPersistenceError("market_intelligence_ablation_write_failed") from exc
    return result.write_performed
