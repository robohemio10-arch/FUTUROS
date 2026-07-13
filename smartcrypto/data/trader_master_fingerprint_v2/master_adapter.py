"""Read-only adapter for the official Trader Master Parquet."""

from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pandas as pd

from .fingerprint_spec import (
    FingerprintValidationError,
    Sha256Hasher,
    canonical_json,
    canonical_trade_id_for,
    normalize_trade_row,
    primary_identity_for,
    row_fingerprint_for,
    sha256_hex,
)


FileHasher = Callable[[Path], str]
AfterReadHook = Callable[[Path], None]

DIRECT_MAPPING_ALIASES: dict[str, tuple[str, ...]] = {
    "venue": ("venue", "exchange_source", "exchange"),
    "market_type": ("market_type",),
    "contract_type": ("contract_type",),
    "settlement_currency": ("settlement_currency",),
    "quantity_unit": ("quantity_unit",),
    "contract_size": ("contract_size",),
    "account_scope_hash": ("account_scope_hash",),
    "order_id_namespace": ("order_id_namespace",),
    "source_trade_id": ("source_trade_id",),
    "order_id": ("order_id",),
    "source": ("source", "source_file"),
    "symbol": ("symbol", "moeda"),
    "side": ("side", "fechar_side"),
    "open_time": ("open_time", "horario_abertura"),
    "close_time": ("close_time", "horario_fechamento"),
    "entry_price": ("entry_price", "preco_abertura"),
    "exit_price": ("exit_price", "preco_fechamento"),
    "quantity": ("quantity", "volume_fechado"),
    "gross_pnl": ("gross_pnl",),
    "trading_fee": ("trading_fee",),
    "funding_fee": ("funding_fee",),
    "net_pnl": ("net_pnl", "pnl_fechado"),
    "epsilon_abs_fonte": ("epsilon_abs_fonte",),
}

FINANCIAL_CANONICAL_FIELDS = (
    "entry_price",
    "exit_price",
    "quantity",
    "contract_size",
    "gross_pnl",
    "trading_fee",
    "funding_fee",
    "net_pnl",
)
NATIVE_IDENTITY_FIELDS = (
    "venue",
    "account_scope_hash",
    "order_id_namespace",
    "source_trade_id",
    "order_id",
)


@dataclass(frozen=True)
class MasterCanonicalRecord:
    row_index: int
    normalized: dict[str, str | None]
    canonical_json: str
    row_fingerprint: str
    primary_identity: dict[str, str] | None
    canonical_trade_id: str
    legacy_identity: dict[str, str]


@dataclass(frozen=True)
class MasterReadBundle:
    report: dict[str, Any]
    canonical_records: tuple[MasterCanonicalRecord, ...]
    unverifiable_rows: tuple[dict[str, Any], ...]


def read_trader_master_readonly(
    *,
    project_root: str | Path,
    trader_master_path: str | Path,
    row_hasher: Sha256Hasher = sha256_hex,
    file_hasher: FileHasher | None = None,
    after_read_hook: AfterReadHook | None = None,
) -> MasterReadBundle:
    """Read the authoritative Parquet through a temporary copy and preserve evidence."""

    root = Path(project_root).resolve()
    requested = Path(trader_master_path)
    source = requested if requested.is_absolute() else root / requested
    display = _display_path(source, root)
    base = {
        "trader_master_path": display,
        "trader_master_temp_copy_used": False,
        "trader_master_sha256_before": None,
        "trader_master_sha256_after": None,
        "trader_master_size_before": None,
        "trader_master_size_after": None,
        "trader_master_hash_preserved": False,
        "trader_master_row_count": 0,
        "trader_master_schema_columns": [],
        "trader_master_schema_dtypes": {},
        "trader_master_null_counts": {},
        "trader_master_duplicate_column_names": [],
        "required_mappings_present": {},
        "financial_fields_available": [],
        "native_identity_fields_available": [],
        "unmapped_master_fields": [],
        "master_valid_fingerprint_row_count": 0,
        "master_unverifiable_row_count": 0,
    }
    path_error = _validate_master_path(root, source)
    if path_error is not None:
        return MasterReadBundle(
            report={**base, "status": "blocked", "reason": path_error},
            canonical_records=(),
            unverifiable_rows=(),
        )

    hasher = file_hasher or file_sha256
    try:
        size_before = source.stat().st_size
        sha_before = hasher(source)
    except OSError as exc:
        return MasterReadBundle(
            report={
                **base,
                "status": "blocked",
                "reason": "trader_master_unreadable",
                "validation_errors": [f"trader_master_unreadable:{type(exc).__name__}"],
            },
            canonical_records=(),
            unverifiable_rows=(),
        )
    try:
        with TemporaryDirectory(prefix="trader-master-readonly-") as temporary:
            copied = Path(temporary) / "trades_master.parquet"
            shutil.copyfile(source, copied)
            frame = pd.read_parquet(copied)
            if after_read_hook is not None:
                after_read_hook(source)
    except Exception as exc:
        return MasterReadBundle(
            report={
                **base,
                "status": "blocked",
                "reason": "trader_master_unreadable",
                "trader_master_sha256_before": sha_before,
                "trader_master_size_before": size_before,
                "trader_master_temp_copy_used": True,
                "validation_errors": [f"trader_master_unreadable:{type(exc).__name__}"],
            },
            canonical_records=(),
            unverifiable_rows=(),
        )

    try:
        size_after = source.stat().st_size
        sha_after = hasher(source)
    except OSError as exc:
        return MasterReadBundle(
            report={
                **base,
                "status": "blocked",
                "reason": "trader_master_changed_during_reconciliation",
                "trader_master_sha256_before": sha_before,
                "trader_master_size_before": size_before,
                "trader_master_temp_copy_used": True,
                "validation_errors": [
                    f"trader_master_post_read_unavailable:{type(exc).__name__}"
                ],
            },
            canonical_records=(),
            unverifiable_rows=(),
        )
    hash_preserved = sha_before == sha_after and size_before == size_after
    schema = inventory_master_schema(frame)
    report = {
        **base,
        **schema,
        "status": "ok" if hash_preserved else "blocked",
        "reason": (
            "trader_master_readonly_copy_ok"
            if hash_preserved
            else "trader_master_changed_during_reconciliation"
        ),
        "trader_master_sha256_before": sha_before,
        "trader_master_sha256_after": sha_after,
        "trader_master_size_before": size_before,
        "trader_master_size_after": size_after,
        "trader_master_hash_preserved": hash_preserved,
        "trader_master_temp_copy_used": True,
    }
    if not hash_preserved:
        return MasterReadBundle(report=report, canonical_records=(), unverifiable_rows=())
    if schema["trader_master_duplicate_column_names"]:
        report.update(
            status="blocked",
            reason="duplicate_trader_master_columns",
            validation_errors=["duplicate_trader_master_columns"],
        )
        return MasterReadBundle(report=report, canonical_records=(), unverifiable_rows=())

    canonical_records: list[MasterCanonicalRecord] = []
    unverifiable_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(frame.to_dict(orient="records")):
        candidate, mapping = map_master_row(row)
        legacy_identity = legacy_identity_for(row)
        try:
            normalized = normalize_trade_row(candidate)
            serialized = canonical_json(normalized)
            fingerprint = row_fingerprint_for(normalized, hasher=row_hasher)
            identity = primary_identity_for(normalized)
            canonical_id = canonical_trade_id_for(
                normalized,
                row_fingerprint=fingerprint,
            )
        except FingerprintValidationError as exc:
            unverifiable_rows.append(
                {
                    "row_index": row_index,
                    "classification": "master_row_unverifiable",
                    "reasons": sorted(set(str(exc).split(";"))),
                    "mapped_source_fields": mapping,
                    "legacy_identity": legacy_identity,
                }
            )
            continue
        canonical_records.append(
            MasterCanonicalRecord(
                row_index=row_index,
                normalized=normalized,
                canonical_json=serialized,
                row_fingerprint=fingerprint,
                primary_identity=identity,
                canonical_trade_id=canonical_id,
                legacy_identity=legacy_identity,
            )
        )

    report.update(
        master_valid_fingerprint_row_count=len(canonical_records),
        master_unverifiable_row_count=len(unverifiable_rows),
    )
    return MasterReadBundle(
        report=report,
        canonical_records=tuple(canonical_records),
        unverifiable_rows=tuple(unverifiable_rows),
    )


def inventory_master_schema(frame: pd.DataFrame) -> dict[str, Any]:
    columns = [str(column) for column in frame.columns]
    counts = Counter(columns)
    duplicate_columns = sorted(column for column, count in counts.items() if count > 1)
    selected = {
        canonical: next((alias for alias in aliases if alias in columns), None)
        for canonical, aliases in DIRECT_MAPPING_ALIASES.items()
    }
    used = {column for column in selected.values() if column is not None}
    return {
        "trader_master_row_count": len(frame),
        "trader_master_schema_columns": columns,
        "trader_master_schema_dtypes": {
            str(column): str(dtype) for column, dtype in frame.dtypes.items()
        },
        "trader_master_null_counts": {
            str(column): int(value) for column, value in frame.isna().sum().items()
        },
        "trader_master_duplicate_column_names": duplicate_columns,
        "required_mappings_present": selected,
        "financial_fields_available": sorted(
            selected[field] for field in FINANCIAL_CANONICAL_FIELDS if selected[field]
        ),
        "native_identity_fields_available": sorted(
            selected[field] for field in NATIVE_IDENTITY_FIELDS if selected[field]
        ),
        "unmapped_master_fields": sorted(set(columns) - used),
    }


def map_master_row(row: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Map only direct semantic aliases; no financial or identity values are derived."""

    candidate: dict[str, Any] = {}
    mapping: dict[str, str] = {}
    for canonical, aliases in DIRECT_MAPPING_ALIASES.items():
        selected = next((alias for alias in aliases if alias in row), None)
        if selected is not None:
            candidate[canonical] = row.get(selected)
            mapping[canonical] = selected
    return candidate, mapping


def legacy_identity_for(row: Mapping[str, Any]) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for name in ("order_id", "source_trade_id", "_dedup_key", "_relaxed_dedup_key"):
        value = row.get(name)
        if value is not None and str(value).strip().casefold() not in {"", "nan", "none", "<na>"}:
            evidence[name] = str(value).strip()
    return evidence


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_master_path(root: Path, source: Path) -> str | None:
    if source.suffix.casefold() != ".parquet":
        return "trader_master_extension_invalid"
    if source.is_symlink():
        return "trader_master_symlink_forbidden"
    try:
        source.resolve().relative_to(root)
    except ValueError:
        return "trader_master_outside_project_root"
    if not source.exists():
        return "trader_master_missing"
    if not source.is_file():
        return "trader_master_not_regular_file"
    return None


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())
