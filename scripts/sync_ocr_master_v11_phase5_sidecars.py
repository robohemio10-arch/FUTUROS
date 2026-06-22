from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


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

SAFETY = {
    "writes_master_xlsx": False,
    "writes_master_parquet": True,
    "writes_compatibility_xlsx": True,
    "changes_training_dataset": False,
    "changes_model": False,
    "sends_orders": False,
    "changes_risk": False,
    "exchange_private_access": False,
}


@dataclass(frozen=True)
class AlignmentPaths:
    project_root: Path
    master_xlsx: Path
    master_parquet: Path
    compatibility_xlsx: Path
    backup_root: Path
    report: Path


def resolve_paths(project_root: str | Path) -> AlignmentPaths:
    root = Path(project_root).resolve()
    return AlignmentPaths(
        project_root=root,
        master_xlsx=root / "data" / "trades" / "trades_master.xlsx",
        master_parquet=root / "data" / "trades" / "trades_master.parquet",
        compatibility_xlsx=root / "data" / "trades" / "trades_excel.xlsx",
        backup_root=root / "data" / "backups",
        report=root / "data" / "reports" / "ocr_master_v11_phase5_source_alignment_report.json",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        if path.suffix.lower() == ".parquet":
            return int(len(pd.read_parquet(path)))
        return int(len(pd.read_excel(path)))
    except (OSError, ValueError):
        return None


def first_available(frame: pd.DataFrame, columns: tuple[str, ...], fallback: str) -> pd.Series:
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
    missing = sorted(set(OCR_TO_PHASE5) - set(master.columns))
    if "fingerprint_operacional" not in master.columns:
        missing.append("fingerprint_operacional")
    if missing:
        raise ValueError(f"missing_ocr_master_columns:{','.join(sorted(set(missing)))}")

    output = pd.DataFrame(index=master.index)
    for source, destination in OCR_TO_PHASE5.items():
        output[destination] = master[source]

    fingerprints = master["fingerprint_operacional"].astype("string").str.strip()
    valid_fingerprints = fingerprints.notna() & fingerprints.ne("") & fingerprints.str.lower().ne("nan")
    if not bool(valid_fingerprints.all()):
        raise ValueError("missing_fingerprint_operacional")
    if bool(fingerprints.duplicated(keep=False).any()):
        raise ValueError("duplicate_fingerprint_operacional")

    output["leverage"] = pd.NA
    output["preco_transacao"] = pd.NA
    output["volume_transacao"] = pd.NA
    output["direcao_liquidez"] = pd.NA
    output["taxa_2"] = pd.NA
    output["horario_transacao"] = output["horario_fechamento"]
    output["source_file"] = first_available(
        master,
        ("candidate_source", "source_full_run_xlsx", "source_file"),
        "ocr_candidate_v1_1",
    )
    output["imported_at"] = first_available(
        master,
        (
            "candidate_generated_at_utc",
            "manual_reviewed_at_final",
            "manual_reviewed_at",
        ),
        generated_at_utc,
    )
    output["_dedup_key"] = fingerprints
    output["_relaxed_dedup_key"] = fingerprints
    output["exchange_source"] = "bitradex"
    output["market_data_source"] = "binance"
    output["ocr_source"] = "bitradex_ocr_candidate_v1_1"
    return output.loc[:, list(PHASE5_COLUMNS)].reset_index(drop=True)


def normalized_frame_digest(frame: pd.DataFrame) -> str:
    normalized = frame.loc[:, list(PHASE5_COLUMNS)].copy()
    for column in normalized.columns:
        normalized[column] = normalized[column].fillna("").astype(str).str.strip()
    hashed = pd.util.hash_pandas_object(normalized, index=False).values.tobytes()
    return hashlib.sha256(hashed).hexdigest()


def existing_matches(path: Path, desired: pd.DataFrame) -> bool:
    if not path.exists():
        return False
    try:
        current = (
            pd.read_parquet(path)
            if path.suffix.lower() == ".parquet"
            else pd.read_excel(path)
        )
    except (OSError, ValueError):
        return False
    if list(current.columns) != list(PHASE5_COLUMNS) or len(current) != len(desired):
        return False
    return normalized_frame_digest(current) == normalized_frame_digest(desired)


def temporary_path(target: Path) -> Path:
    return target.with_name(f".{target.stem}.alignment_tmp{target.suffix}")


def validate_written_sidecar(path: Path, expected_rows: int) -> list[str]:
    frame = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_excel(path)
    errors: list[str] = []
    if len(frame) != expected_rows:
        errors.append(f"{path.name}:rows:{len(frame)}!={expected_rows}")
    if list(frame.columns) != list(PHASE5_COLUMNS):
        errors.append(f"{path.name}:columns_do_not_match_phase5_contract")
    fingerprints = frame["_dedup_key"].astype("string").str.strip()
    if fingerprints.isna().any() or fingerprints.eq("").any():
        errors.append(f"{path.name}:missing_dedup_key")
    if fingerprints.duplicated(keep=False).any():
        errors.append(f"{path.name}:duplicate_dedup_key")
    return errors


def create_backup(paths: AlignmentPaths, run_stamp: str) -> tuple[Path, list[str]]:
    backup_dir = paths.backup_root / f"ocr_master_v11_phase5_alignment_{run_stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_files: list[str] = []
    for source in (paths.compatibility_xlsx, paths.master_parquet):
        if source.exists():
            target = backup_dir / source.name
            shutil.copy2(source, target)
            backup_files.append(target.as_posix())
    return backup_dir, backup_files


def write_sidecars_atomically(
    paths: AlignmentPaths,
    frame: pd.DataFrame,
    expected_rows: int,
) -> None:
    paths.compatibility_xlsx.parent.mkdir(parents=True, exist_ok=True)
    xlsx_tmp = temporary_path(paths.compatibility_xlsx)
    parquet_tmp = temporary_path(paths.master_parquet)
    try:
        frame.to_excel(xlsx_tmp, index=False)
        frame.to_parquet(parquet_tmp, index=False)
        errors = validate_written_sidecar(xlsx_tmp, expected_rows)
        errors.extend(validate_written_sidecar(parquet_tmp, expected_rows))
        if errors:
            raise ValueError(";".join(errors))
        xlsx_tmp.replace(paths.compatibility_xlsx)
        parquet_tmp.replace(paths.master_parquet)
    finally:
        xlsx_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def base_report(
    paths: AlignmentPaths,
    expected_sha256: str,
    actual_sha256: str | None,
    expected_rows: int,
    master_rows: int | None,
    compatibility_rows_before: int | None,
    master_parquet_rows_before: int | None,
    no_write: bool,
    generated_at_utc: str,
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "validation_failed",
        "master_xlsx_path": paths.master_xlsx.as_posix(),
        "master_sha256_expected": expected_sha256,
        "master_sha256_actual": actual_sha256,
        "expected_rows": expected_rows,
        "master_rows": master_rows,
        "compatibility_rows_before": compatibility_rows_before,
        "master_parquet_rows_before": master_parquet_rows_before,
        "compatibility_rows_after": compatibility_rows_before,
        "master_parquet_rows_after": master_parquet_rows_before,
        "backup_created": False,
        "backup_dir": None,
        "backup_files": [],
        "no_write": no_write,
        "would_write": False,
        "write_performed": False,
        "validation_errors": [],
        "phase5_columns": list(PHASE5_COLUMNS),
        "generated_at_utc": generated_at_utc,
        "safety": dict(SAFETY),
        **SAFETY,
    }


def sync_ocr_master_v11_phase5_sidecars(
    project_root: str | Path,
    expected_master_sha256: str,
    expected_rows: int,
    *,
    no_write: bool,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    paths = resolve_paths(project_root)
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = now.isoformat().replace("+00:00", "Z")
    actual_sha = sha256_file(paths.master_xlsx) if paths.master_xlsx.exists() else None
    expected_sha_normalized = expected_master_sha256.strip().casefold()
    actual_sha_normalized = actual_sha.casefold() if actual_sha is not None else None
    master: pd.DataFrame | None = None
    master_rows: int | None = None
    validation_errors: list[str] = []

    if not paths.master_xlsx.exists():
        validation_errors.append("master_xlsx_not_found")
    else:
        try:
            master = pd.read_excel(paths.master_xlsx)
            master_rows = int(len(master))
        except (OSError, ValueError) as exc:
            validation_errors.append(f"master_xlsx_read_error:{type(exc).__name__}")

    report = base_report(
        paths,
        expected_master_sha256,
        actual_sha,
        expected_rows,
        master_rows,
        read_rows(paths.compatibility_xlsx),
        read_rows(paths.master_parquet),
        no_write,
        generated_at,
    )
    if actual_sha_normalized != expected_sha_normalized:
        validation_errors.append("master_sha256_mismatch")
    if master_rows != expected_rows:
        validation_errors.append(f"master_rows_mismatch:{master_rows}!={expected_rows}")

    compatibility: pd.DataFrame | None = None
    if master is not None and not validation_errors:
        try:
            compatibility = build_phase5_compatibility_frame(master, generated_at)
        except ValueError as exc:
            validation_errors.append(str(exc))

    if compatibility is not None:
        report["would_write"] = not (
            existing_matches(paths.compatibility_xlsx, compatibility)
            and existing_matches(paths.master_parquet, compatibility)
        )
        report["compatibility_rows_after"] = int(len(compatibility))
        report["master_parquet_rows_after"] = int(len(compatibility))

    if validation_errors:
        report["validation_errors"] = sorted(set(validation_errors))
        write_report(paths.report, report)
        return report

    if compatibility is None:
        report["validation_errors"] = ["compatibility_frame_not_built"]
        write_report(paths.report, report)
        return report

    if no_write:
        report.update(status="ok", reason="dry_run_validation_ok")
        write_report(paths.report, report)
        return report

    if not report["would_write"]:
        report.update(status="ok", reason="phase5_sidecars_already_aligned")
        write_report(paths.report, report)
        return report

    run_stamp = now.strftime("%Y%m%d_%H%M%S")
    try:
        backup_dir, backup_files = create_backup(paths, run_stamp)
        report.update(
            backup_created=True,
            backup_dir=backup_dir.as_posix(),
            backup_files=backup_files,
        )
        write_sidecars_atomically(paths, compatibility, expected_rows)
        post_errors = validate_written_sidecar(paths.compatibility_xlsx, expected_rows)
        post_errors.extend(validate_written_sidecar(paths.master_parquet, expected_rows))
        if sha256_file(paths.master_xlsx).casefold() != expected_sha_normalized:
            post_errors.append("master_xlsx_changed_during_alignment")
        if post_errors:
            report["validation_errors"] = sorted(set(post_errors))
            report["reason"] = "post_write_validation_failed"
        else:
            report.update(
                status="ok",
                reason="phase5_sidecars_aligned",
                write_performed=True,
                compatibility_rows_after=read_rows(paths.compatibility_xlsx),
                master_parquet_rows_after=read_rows(paths.master_parquet),
            )
    except (OSError, ValueError) as exc:
        report["reason"] = "sidecar_write_failed"
        report["validation_errors"] = [f"{type(exc).__name__}:{exc}"]

    write_report(paths.report, report)
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align OCR Master V1.1 read-only source with canonical Phase5 sidecars."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--expected-master-sha256", default=DEFAULT_MASTER_SHA256)
    parser.add_argument("--expected-rows", type=int, default=DEFAULT_EXPECTED_ROWS)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = sync_ocr_master_v11_phase5_sidecars(
        args.project_root,
        args.expected_master_sha256,
        args.expected_rows,
        no_write=args.no_write,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    if report["status"] != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
