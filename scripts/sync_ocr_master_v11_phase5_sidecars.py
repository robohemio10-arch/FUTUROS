#!/usr/bin/env python3
"""Fail-closed compatibility audit for the retired Phase 5 sidecar synchronizer."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smartcrypto.data.trader_master_fingerprint_v2.legacy_master_governance import (  # noqa: E402
    DEFAULT_MASTER,
)
from smartcrypto.data.trader_master_fingerprint_v2.master_adapter import (  # noqa: E402
    read_trader_master_readonly,
)


DEFAULT_MASTER_SHA256 = "83e2d17db317cc84b2bd39e00a961bd8d568c4375c5a4a113f6a26df58972e90"
DEFAULT_EXPECTED_ROWS = 3058
PHASE5_COLUMNS = (
    "moeda",
    "fechar_side",
    "leverage",
    "order_id",
    "pnl_fechado",
    "taxa_lucros_perdas_fechados_pct",
    "preco_abertura",
    "preco_fechamento",
    "volume_posicao",
    "volume_fechado",
    "horario_abertura",
    "horario_fechamento",
    "taxa_1",
    "preco_transacao",
    "volume_transacao",
    "direcao_liquidez",
    "taxa_2",
    "horario_transacao",
    "source_file",
    "imported_at",
    "_dedup_key",
    "_relaxed_dedup_key",
    "exchange_source",
    "market_data_source",
    "ocr_source",
)
OCR_TO_PHASE5 = {
    "11_moeda": "moeda",
    "12_fechar_long_short": "fechar_side",
    "10_numero_do_pedido": "order_id",
    "1_pnl_fechado": "pnl_fechado",
    "2_taxa_lucros_perdas_fechados": "taxa_lucros_perdas_fechados_pct",
    "3_preco_de_abertura": "preco_abertura",
    "4_preco_de_fechamento": "preco_fechamento",
    "5_volume_de_posicao": "volume_posicao",
    "6_volume_fechado": "volume_fechado",
    "7_horario_de_abertura": "horario_abertura",
    "8_horario_de_fechamento": "horario_fechamento",
    "9_taxa": "taxa_1",
}


def _first_available(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    fallback: str,
) -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    for column in columns:
        if column not in frame.columns:
            continue
        candidate = frame[column]
        present = candidate.notna() & candidate.astype(str).str.strip().ne("")
        result = result.where(result.notna(), candidate.where(present))
    return result.fillna(fallback)


def build_phase5_compatibility_frame(
    master: pd.DataFrame,
    generated_at_utc: str,
) -> pd.DataFrame:
    """Build a frame in memory only; this function has no persistence authority."""

    missing = sorted(set(OCR_TO_PHASE5) - set(master.columns))
    if "fingerprint_operacional" not in master.columns:
        missing.append("fingerprint_operacional")
    if missing:
        raise ValueError(f"missing_ocr_master_columns:{','.join(sorted(set(missing)))}")
    output = pd.DataFrame(index=master.index)
    for source, destination in OCR_TO_PHASE5.items():
        output[destination] = master[source]
    fingerprints = master["fingerprint_operacional"].astype("string").str.strip()
    valid = fingerprints.notna() & fingerprints.ne("") & fingerprints.str.lower().ne("nan")
    if not bool(valid.all()):
        raise ValueError("missing_fingerprint_operacional")
    if bool(fingerprints.duplicated(keep=False).any()):
        raise ValueError("duplicate_fingerprint_operacional")
    output["leverage"] = pd.NA
    output["preco_transacao"] = pd.NA
    output["volume_transacao"] = pd.NA
    output["direcao_liquidez"] = pd.NA
    output["taxa_2"] = pd.NA
    output["horario_transacao"] = output["horario_fechamento"]
    output["source_file"] = _first_available(
        master,
        ("candidate_source", "source_full_run_xlsx", "source_file"),
        "ocr_candidate_v1_1",
    )
    output["imported_at"] = _first_available(
        master,
        ("candidate_generated_at_utc", "manual_reviewed_at_final", "manual_reviewed_at"),
        generated_at_utc,
    )
    output["_dedup_key"] = fingerprints
    output["_relaxed_dedup_key"] = fingerprints
    output["exchange_source"] = "bitradex"
    output["market_data_source"] = "binance"
    output["ocr_source"] = "bitradex_ocr_candidate_v1_1"
    return output.loc[:, list(PHASE5_COLUMNS)].reset_index(drop=True)


def sync_ocr_master_v11_phase5_sidecars(
    project_root: str | Path,
    expected_master_sha256: str,
    expected_rows: int,
    *,
    no_write: bool,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Inspect the pinned artifact and reject the retired sidecar write path."""

    bundle = read_trader_master_readonly(
        project_root=project_root,
        trader_master_path=DEFAULT_MASTER,
    )
    adapter_report = bundle.report
    actual_hash = adapter_report.get("trader_master_sha256_before")
    actual_rows = int(adapter_report.get("trader_master_row_count", 0))
    validation_errors: list[str] = []
    if adapter_report.get("status") != "ok":
        validation_errors.append(
            f"legacy_master_read_blocked:{adapter_report.get('reason', 'unknown')}"
        )
    if actual_hash is not None and str(actual_hash).casefold() != expected_master_sha256.casefold():
        validation_errors.append("master_sha256_mismatch")
    if actual_rows != expected_rows:
        validation_errors.append(f"master_rows_mismatch:{actual_rows}!={expected_rows}")
    if not no_write:
        validation_errors.append("legacy_master_sidecar_write_forbidden")

    reason = (
        "legacy_master_sidecar_sync_retired"
        if no_write and not validation_errors
        else "legacy_master_sidecar_sync_blocked"
    )
    return {
        "status": "blocked",
        "reason": reason,
        "decision": "LEGACY_MASTER_SIDECAR_WRITE_FORBIDDEN",
        "generated_at_utc": (now_utc or datetime.now(UTC)).isoformat(),
        "master_sha256_expected": expected_master_sha256,
        "master_sha256_actual": actual_hash,
        "expected_rows": expected_rows,
        "master_rows": actual_rows,
        "legacy_master_readonly": adapter_report,
        "unverifiable_row_count": len(bundle.unverifiable_rows),
        "unverifiable_rows_preserved": len(bundle.unverifiable_rows) == int(
            adapter_report.get("master_unverifiable_row_count", 0)
        ),
        "no_write": no_write,
        "would_write": False,
        "write_performed": False,
        "backup_created": False,
        "backup_files": [],
        "writes_master_xlsx": False,
        "writes_master_parquet": False,
        "writes_compatibility_xlsx": False,
        "operational_authority": False,
        "validation_errors": sorted(set(validation_errors)),
        "changes_training_dataset": False,
        "changes_model": False,
        "sends_orders": False,
        "changes_risk": False,
        "exchange_private_access": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--expected-master-sha256", default=DEFAULT_MASTER_SHA256)
    parser.add_argument("--expected-rows", type=int, default=DEFAULT_EXPECTED_ROWS)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = sync_ocr_master_v11_phase5_sidecars(
        args.project_root,
        args.expected_master_sha256,
        args.expected_rows,
        no_write=args.no_write,
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.json else 2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
