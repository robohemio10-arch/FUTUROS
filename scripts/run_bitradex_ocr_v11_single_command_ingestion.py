#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
IMPORT_READY_NAMES = (
    "BITRADEX_OCR_PHASE5_IMPORT_READY.csv",
    "BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx",
)
STAGING_AUDIT_NAME = "PROJECT_STAGING_AUDIT_SUMMARY.json"
PREVIEW_NAME = "BITRADEX_OCR_IMPORT_PREVIEW_SUMMARY.json"
APPLY_SUMMARY_NAME = "APPLY_BITRADEX_OCR_ORDERID_SYNTHETIC_V5_SUMMARY.json"
POST_IMPORT_AUDIT_NAME = "POST_IMPORT_TRADES_MASTER_AUDIT_ORDERID_SYNTHETIC_V5.json"
ORDER_ID_RE = re.compile(r"^[0-9a-f]{24}$")

OFFICIAL_COLUMNS = (
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
OCR_V11_SOURCE_COLUMNS = (
    "11_moeda",
    "12_fechar_long_short",
    "10_numero_do_pedido",
    "1_pnl_fechado",
    "2_taxa_lucros_perdas_fechados",
    "3_preco_de_abertura",
    "4_preco_de_fechamento",
    "5_volume_de_posicao",
    "6_volume_fechado",
    "7_horario_de_abertura",
    "8_horario_de_fechamento",
    "9_taxa",
    "fingerprint_operacional",
)
SAFETY_FLAGS: dict[str, bool] = {
    "paper_only": True,
    "shadow_only": True,
    "live_trading_enabled": False,
    "order_submission_enabled": False,
    "real_order_submission_enabled": False,
    "exchange_private_access": False,
    "sends_orders": False,
    "changes_risk": False,
    "changes_model": False,
}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class OrchestratorPaths:
    project_root: Path
    input_dir: Path
    package_dir: Path
    report_path: Path
    master_xlsx: Path
    ocr_script: Path
    apply_script: Path
    sync_script: Path
    phase5_script: Path


@dataclass(frozen=True)
class OrchestratorOptions:
    paths: OrchestratorPaths
    apply_import: bool = False
    run_phase5: bool = False
    timeout_seconds: int = 900
    lang: str = "eng"

    @property
    def dry_run(self) -> bool:
        return not self.apply_import


CommandExecutor = Callable[[Sequence[str], Path, int], CommandResult]


def run_command(argv: Sequence[str], cwd: Path, timeout_seconds: int) -> CommandResult:
    command = [str(value) for value in argv]
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            tuple(command),
            124,
            str(exc.stdout or ""),
            str(exc.stderr or ""),
            timed_out=True,
        )
    except OSError as exc:
        return CommandResult(tuple(command), 127, "", f"{type(exc).__name__}:{exc}")
    return CommandResult(
        tuple(command),
        completed.returncode,
        completed.stdout.strip(),
        completed.stderr.strip(),
    )


def resolve_paths(
    project_root: str | Path,
    input_dir: str | Path,
    package_dir: str | Path | None,
    report_path: str | Path | None,
) -> OrchestratorPaths:
    root = Path(project_root).expanduser().resolve()
    package = (
        Path(package_dir).expanduser().resolve()
        if package_dir
        else root / "data" / "staging" / "bitradex_ocr_v11_next_lot"
    )
    report = (
        Path(report_path).expanduser().resolve()
        if report_path
        else root / "data" / "reports" / "bitradex_ocr_v11_single_command_ingestion_report.json"
    )
    return OrchestratorPaths(
        project_root=root,
        input_dir=Path(input_dir).expanduser().resolve(),
        package_dir=package,
        report_path=report,
        master_xlsx=root / "data" / "trades" / "trades_master.xlsx",
        ocr_script=root / "scripts" / "ocr_bitradex_images_to_review.py",
        apply_script=root / "scripts" / "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py",
        sync_script=root / "scripts" / "sync_ocr_master_v11_phase5_sidecars.py",
        phase5_script=root / "scripts" / "rebuild_phase5_datasets.py",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_images(input_dir: Path) -> list[Path]:
    if not input_dir.exists() or not input_dir.is_dir():
        return []
    return sorted(
        (
            path.resolve()
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.relative_to(input_dir).as_posix().casefold(),
    )


def image_inventory(images: Sequence[Path], input_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": image.relative_to(input_dir).as_posix(),
            "bytes": image.stat().st_size,
            "sha256": sha256_file(image),
        }
        for image in images
    ]


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_root_not_object:{path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    if path.suffix.casefold() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    raise ValueError(f"unsupported_candidate_format:{path.suffix}")


def find_candidate(package_dir: Path) -> Path | None:
    for name in IMPORT_READY_NAMES:
        candidate = package_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def normalize_order_id(value: object) -> str:
    text = "" if value is None else str(value).strip().casefold()
    return text[:-2] if text.endswith(".0") else text


def normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace("/", "").replace("_", "")


def normalize_side(value: object) -> str:
    text = str(value or "").strip().casefold().replace("fechar", "").strip()
    if "long" in text:
        return "long"
    if "short" in text:
        return "short"
    return text


def numeric_missing(series: pd.Series) -> pd.Series:
    normalized = (
        series.fillna("")
        .astype(str)
        .str.replace("USDT", "", case=False, regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
    )
    return pd.to_numeric(normalized, errors="coerce").isna()


def validate_candidate(candidate_path: Path, master: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidate = read_table(candidate_path)
    candidate.columns = [str(column).strip() for column in candidate.columns]
    errors: list[str] = []
    missing_columns = sorted(set(OFFICIAL_COLUMNS) - set(candidate.columns))
    if missing_columns:
        errors.append("missing_official_columns:" + ",".join(missing_columns))

    ocr_v11_master = set(OCR_V11_SOURCE_COLUMNS).issubset(master.columns)
    missing_ocr_source = sorted(set(OCR_V11_SOURCE_COLUMNS) - set(candidate.columns))
    if ocr_v11_master and missing_ocr_source:
        errors.append("missing_ocr_v11_source_columns:" + ",".join(missing_ocr_source))

    internal_duplicates = 0
    non_hex_order_ids = 0
    invalid_rows = len(candidate) if missing_columns else 0
    if not missing_columns:
        order_ids = candidate["order_id"].map(normalize_order_id)
        internal_duplicates = int(order_ids[order_ids.ne("")].duplicated(keep=False).sum())
        non_hex_order_ids = int((~order_ids.map(lambda value: bool(ORDER_ID_RE.fullmatch(value)))).sum())

        invalid = pd.Series(False, index=candidate.index, dtype=bool)
        invalid |= ~candidate["moeda"].map(normalize_symbol).isin({"BTCUSDT", "ETHUSDT"})
        invalid |= ~candidate["fechar_side"].map(normalize_side).isin({"long", "short"})
        invalid |= numeric_missing(candidate["pnl_fechado"])
        invalid |= numeric_missing(candidate["preco_abertura"])
        invalid |= numeric_missing(candidate["preco_fechamento"])
        invalid |= numeric_missing(candidate["volume_posicao"])
        invalid |= numeric_missing(candidate["volume_fechado"])
        invalid |= pd.to_datetime(candidate["horario_abertura"], errors="coerce", utc=True).isna()
        invalid |= pd.to_datetime(candidate["horario_fechamento"], errors="coerce", utc=True).isna()
        invalid |= order_ids.eq("")
        invalid_rows = int(invalid.sum())

    if candidate.empty:
        errors.append("empty_candidate")
    if internal_duplicates:
        errors.append(f"duplicate_internal_order_id_rows:{internal_duplicates}")
    if non_hex_order_ids:
        errors.append(f"non_hex24_order_id_rows:{non_hex_order_ids}")
    if invalid_rows:
        errors.append(f"invalid_critical_rows:{invalid_rows}")

    audit = {
        "status": "ok" if not errors else "blocked",
        "reason": "candidate_schema_valid" if not errors else "candidate_schema_invalid",
        "candidate_path": str(candidate_path),
        "candidate_sha256": sha256_file(candidate_path),
        "candidate_rows": int(len(candidate)),
        "invalid_rows": invalid_rows,
        "duplicate_internal_order_id_rows": internal_duplicates,
        "duplicate_against_trades_master_rows": 0,
        "non_hex24_order_id_rows": non_hex_order_ids,
        "ocr_v11_source_contract_required": ocr_v11_master,
        "validation_errors": errors,
        "writes_trades_master": False,
        **SAFETY_FLAGS,
    }
    return candidate, audit


def master_order_ids(master: pd.DataFrame) -> set[str]:
    if "order_id" in master.columns:
        series = master["order_id"]
    elif "10_numero_do_pedido" in master.columns:
        series = master["10_numero_do_pedido"]
    else:
        return set()
    values = {normalize_order_id(value) for value in series}
    values.discard("")
    values.discard("nan")
    return values


def build_preview(candidate: pd.DataFrame, master: pd.DataFrame) -> dict[str, Any]:
    order_ids = (
        candidate["order_id"].map(normalize_order_id)
        if "order_id" in candidate.columns
        else pd.Series("", index=candidate.index)
    )
    existing = master_order_ids(master)
    duplicate_against_master = int(order_ids.isin(existing).sum())
    internal_duplicates = int(order_ids[order_ids.ne("")].duplicated(keep=False).sum())
    rows_before = int(len(master))
    incoming_rows = int(len(candidate))
    expected_rows_after = rows_before + incoming_rows - duplicate_against_master
    problem_rows = duplicate_against_master + internal_duplicates
    errors: list[str] = []
    if duplicate_against_master:
        errors.append(f"duplicate_against_trades_master_rows:{duplicate_against_master}")
    if internal_duplicates:
        errors.append(f"duplicate_internal_order_id_rows:{internal_duplicates}")
    return {
        "status": "ok" if not errors else "blocked",
        "reason": "preview_validation_ok" if not errors else "preview_validation_failed",
        "preview_only": True,
        "writes_trades_master": False,
        "rows_before": rows_before,
        "incoming_rows": incoming_rows,
        "rows_after": rows_before,
        "expected_rows_after": expected_rows_after,
        "duplicate_internal_order_id_rows": internal_duplicates,
        "duplicate_against_trades_master_rows": duplicate_against_master,
        "problem_rows": problem_rows,
        "validation_errors": errors,
        **SAFETY_FLAGS,
    }


def candidate_source_matches(
    candidate: pd.DataFrame,
    images: Sequence[Path],
) -> tuple[int, list[str]]:
    source_column = "source_file" if "source_file" in candidate.columns else "imagem"
    if source_column not in candidate.columns:
        return 0, ["candidate_source_column_missing"]
    image_names = {image.name.casefold() for image in images}
    source_names = candidate[source_column].fillna("").astype(str).map(
        lambda value: Path(value.strip()).name.casefold()
    )
    matched = source_names.isin(image_names)
    unmatched = sorted(set(source_names[~matched].tolist()))
    return int(matched.sum()), [value for value in unmatched if value]


def create_orchestrator_backup(paths: OrchestratorPaths) -> tuple[Path, list[str]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = paths.project_root / "data" / "backups" / f"bitradex_ocr_v11_orchestrator_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    for source in (
        paths.master_xlsx,
        paths.project_root / "data" / "trades" / "trades_master.parquet",
        paths.project_root / "data" / "trades" / "trades_excel.xlsx",
    ):
        if not source.exists():
            continue
        destination = backup_dir / source.name
        shutil.copy2(source, destination)
        if sha256_file(source) != sha256_file(destination):
            raise OSError(f"backup_hash_mismatch:{source.name}")
        copied.append(str(destination))
    if not (backup_dir / paths.master_xlsx.name).exists():
        raise OSError("master_backup_missing")
    return backup_dir, copied


def parse_command_json(result: CommandResult) -> dict[str, Any]:
    if not result.stdout:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def base_report(options: OrchestratorOptions) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": "not_started",
        "dry_run": options.dry_run,
        "apply_import": options.apply_import,
        "run_phase5": options.run_phase5,
        "input_dir": str(options.paths.input_dir),
        "input_image_count": 0,
        "input_images": [],
        "package_dir": str(options.paths.package_dir),
        "candidate_path": None,
        "staging_status": "not_run",
        "candidate_status": "not_run",
        "preview_status": "not_run",
        "import_status": "not_run",
        "post_import_audit_status": "not_run",
        "sidecar_sync_status": "not_run",
        "phase5_status": "not_run",
        "rows_before": 0,
        "incoming_rows": 0,
        "rows_after": 0,
        "expected_rows_after": 0,
        "trade_enriched_rows": None,
        "training_dataset_rows": None,
        "master_sha256_before": None,
        "master_sha256_after": None,
        "backup_path": None,
        "backup_files": [],
        "official_import_backup_path": None,
        "rollback_command": None,
        "validations_executed": [],
        "blockers": [],
        **SAFETY_FLAGS,
    }


def finish(report: dict[str, Any], path: Path, status: str, reason: str) -> dict[str, Any]:
    report["status"] = status
    report["reason"] = reason
    write_json(path, report)
    return report


def command_failure(
    report: dict[str, Any],
    report_path: Path,
    result: CommandResult,
    stage: str,
) -> dict[str, Any]:
    report["blockers"].append(
        {
            "stage": stage,
            "reason": "subprocess_timeout" if result.timed_out else "subprocess_failed",
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:],
        }
    )
    reason = f"{stage}_timeout" if result.timed_out else f"{stage}_failed"
    return finish(report, report_path, "failed", reason)


def git_worktree_clean(
    root: Path,
    executor: CommandExecutor,
    timeout_seconds: int,
) -> tuple[bool | None, CommandResult]:
    result = executor(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        root,
        timeout_seconds,
    )
    if result.returncode != 0 or result.timed_out:
        return None, result
    return not bool(result.stdout.strip()), result


def run_ingestion(
    options: OrchestratorOptions,
    *,
    executor: CommandExecutor = run_command,
) -> dict[str, Any]:
    paths = options.paths
    report = base_report(options)

    if options.run_phase5 and not options.apply_import:
        report["blockers"].append("run_phase5_requires_apply_import")
        return finish(report, paths.report_path, "blocked", "run_phase5_requires_apply_import")

    if not paths.project_root.exists():
        report["blockers"].append("project_root_not_found")
        return finish(report, paths.report_path, "blocked", "project_root_not_found")
    if not paths.input_dir.exists() or not paths.input_dir.is_dir():
        report["blockers"].append("input_dir_not_found")
        return finish(report, paths.report_path, "blocked", "input_dir_not_found")
    if not paths.master_xlsx.exists():
        report["blockers"].append("trades_master_not_found")
        return finish(report, paths.report_path, "blocked", "trades_master_not_found")
    if not paths.ocr_script.exists():
        report["blockers"].append("missing_official_ocr_stage_script")
        return finish(report, paths.report_path, "blocked", "missing_official_ocr_stage_script")

    required_scripts = [paths.apply_script, paths.sync_script]
    if options.run_phase5:
        required_scripts.append(paths.phase5_script)
    missing_scripts = [str(path) for path in required_scripts if not path.exists()]
    if missing_scripts:
        report["blockers"].extend(missing_scripts)
        return finish(report, paths.report_path, "blocked", "missing_required_official_script")
    report["validations_executed"].append("required_paths")

    images = discover_images(paths.input_dir)
    report["input_image_count"] = len(images)
    if not images:
        report["blockers"].append("input_dir_has_no_supported_images")
        return finish(report, paths.report_path, "blocked", "empty_input_dir")
    report["input_images"] = image_inventory(images, paths.input_dir)
    report["validations_executed"].append("deterministic_image_discovery_and_hashing")

    if options.apply_import:
        clean, git_result = git_worktree_clean(
            paths.project_root,
            executor,
            options.timeout_seconds,
        )
        if clean is None:
            return command_failure(report, paths.report_path, git_result, "git_worktree_check")
        if not clean:
            report["blockers"].append("dirty_git_worktree")
            return finish(report, paths.report_path, "blocked", "dirty_git_worktree")
        report["validations_executed"].append("clean_git_worktree_before_write")

    paths.package_dir.mkdir(parents=True, exist_ok=True)
    ocr_command = [
        sys.executable,
        str(paths.ocr_script),
        "--input-dir",
        str(paths.input_dir),
        "--output-dir",
        str(paths.package_dir),
        "--report",
        str(paths.package_dir / "bitradex_ocr_summary.json"),
        "--lang",
        options.lang,
    ]
    if options.dry_run:
        ocr_command.extend(["--dry-run", "--no-xlsx"])
    ocr_result = executor(ocr_command, paths.project_root, options.timeout_seconds)
    if ocr_result.returncode != 0 or ocr_result.timed_out:
        report["staging_status"] = "failed"
        return command_failure(report, paths.report_path, ocr_result, "ocr_staging")
    ocr_payload = parse_command_json(ocr_result)
    report["staging_status"] = str(ocr_payload.get("status") or "ok")
    report["validations_executed"].append("official_ocr_review_stage")

    candidate_path = find_candidate(paths.package_dir)
    if candidate_path is None:
        report["candidate_status"] = "blocked"
        report["blockers"].append("missing_import_ready_candidate")
        return finish(report, paths.report_path, "blocked", "missing_import_ready_candidate")
    report["candidate_path"] = str(candidate_path)

    try:
        master = pd.read_excel(paths.master_xlsx, dtype=str, keep_default_na=False)
        candidate, candidate_audit = validate_candidate(candidate_path, master)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        report["candidate_status"] = "failed"
        report["blockers"].append(f"candidate_read_error:{type(exc).__name__}")
        return finish(report, paths.report_path, "failed", "candidate_read_failed")

    preview = build_preview(candidate, master)
    source_match_rows, unmatched_sources = candidate_source_matches(candidate, images)
    candidate_audit["input_source_match_rows"] = source_match_rows
    candidate_audit["unmatched_input_sources"] = unmatched_sources
    if source_match_rows != len(candidate):
        candidate_audit["status"] = "blocked"
        candidate_audit["reason"] = "candidate_not_linked_to_current_input"
        candidate_audit["validation_errors"].append(
            f"candidate_input_source_mismatch:{len(candidate) - source_match_rows}"
        )
    candidate_audit["duplicate_against_trades_master_rows"] = preview[
        "duplicate_against_trades_master_rows"
    ]
    if preview["duplicate_against_trades_master_rows"]:
        candidate_audit["status"] = "blocked"
        candidate_audit["reason"] = "candidate_duplicates_master"
        candidate_audit["validation_errors"].append(
            f"duplicate_against_trades_master_rows:{preview['duplicate_against_trades_master_rows']}"
        )
    write_json(paths.package_dir / STAGING_AUDIT_NAME, candidate_audit)
    write_json(paths.package_dir / PREVIEW_NAME, preview)
    report["candidate_status"] = candidate_audit["status"]
    report["preview_status"] = preview["status"]
    report["rows_before"] = preview["rows_before"]
    report["incoming_rows"] = preview["incoming_rows"]
    report["rows_after"] = preview["rows_after"]
    report["expected_rows_after"] = preview["expected_rows_after"]
    report["master_sha256_before"] = sha256_file(paths.master_xlsx)
    report["validations_executed"].extend(
        ["candidate_schema_and_critical_fields", "preview_against_trades_master"]
    )
    candidate_errors = list(candidate_audit["validation_errors"])
    preview_errors = list(preview["validation_errors"])
    if candidate_errors or preview_errors:
        report["blockers"].extend(sorted(set(candidate_errors + preview_errors)))
        return finish(report, paths.report_path, "blocked", "candidate_or_preview_blocked")

    if options.dry_run:
        report["import_status"] = "not_run_dry_run"
        return finish(report, paths.report_path, "ok", "dry_run_preview_ok")

    try:
        backup_dir, backup_files = create_orchestrator_backup(paths)
    except OSError as exc:
        report["blockers"].append(f"orchestrator_backup_failed:{exc}")
        return finish(report, paths.report_path, "failed", "orchestrator_backup_failed")
    report["backup_path"] = str(backup_dir)
    report["backup_files"] = backup_files
    report["rollback_command"] = (
        f"Copy-Item -Force '{backup_dir / paths.master_xlsx.name}' '{paths.master_xlsx}'"
    )
    report["validations_executed"].append("orchestrator_backup_before_official_import")

    apply_command = [
        sys.executable,
        str(paths.apply_script),
        "--package-dir",
        str(paths.package_dir),
        "--project-root",
        str(paths.project_root),
        "--json",
    ]
    apply_result = executor(apply_command, paths.project_root, options.timeout_seconds)
    if apply_result.returncode != 0 or apply_result.timed_out:
        report["import_status"] = "failed"
        return command_failure(report, paths.report_path, apply_result, "official_import")
    apply_summary_path = paths.package_dir / APPLY_SUMMARY_NAME
    try:
        apply_summary = (
            read_json(apply_summary_path)
            if apply_summary_path.exists()
            else parse_command_json(apply_result)
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report["blockers"].append(f"invalid_apply_summary:{type(exc).__name__}")
        return finish(report, paths.report_path, "failed", "invalid_apply_summary")

    report["import_status"] = str(apply_summary.get("status") or "failed")
    report["rows_after"] = int(apply_summary.get("rows_after") or 0)
    report["official_import_backup_path"] = apply_summary.get("backup_dir")
    imported_rows = int(apply_summary.get("imported_rows") or 0)
    if report["import_status"] not in {"ok", "idempotent_noop"}:
        report["blockers"].extend(list(apply_summary.get("validation_errors") or []))
        return finish(report, paths.report_path, "blocked", "official_import_blocked")
    if imported_rows > 0:
        official_backup_path = Path(str(report["official_import_backup_path"] or ""))
        if not apply_summary.get("backup_created") or not official_backup_path.is_dir():
            report["blockers"].append("mandatory_backup_missing_after_import")
            return finish(report, paths.report_path, "blocked", "mandatory_backup_missing")
    if report["rows_after"] != report["expected_rows_after"]:
        report["blockers"].append("rows_after_does_not_match_preview")
        return finish(report, paths.report_path, "blocked", "post_import_rows_mismatch")
    report["validations_executed"].append("official_import_with_mandatory_backup")

    post_audit_path = paths.package_dir / POST_IMPORT_AUDIT_NAME
    try:
        post_audit = read_json(post_audit_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report["blockers"].append(f"invalid_post_import_audit:{type(exc).__name__}")
        return finish(report, paths.report_path, "failed", "post_import_audit_missing_or_invalid")
    report["post_import_audit_status"] = str(post_audit.get("status") or "failed")
    if report["post_import_audit_status"] not in {"ok", "idempotent_noop"}:
        report["blockers"].extend(list(post_audit.get("validation_errors") or []))
        return finish(report, paths.report_path, "blocked", "post_import_audit_blocked")
    if int(post_audit.get("duplicate_order_id_rows_after") or 0) != 0:
        report["blockers"].append("post_import_duplicate_order_ids")
        return finish(report, paths.report_path, "blocked", "post_import_duplicate_order_ids")
    report["validations_executed"].append("post_import_audit")

    report["master_sha256_after"] = sha256_file(paths.master_xlsx)
    sync_command = [
        sys.executable,
        str(paths.sync_script),
        "--project-root",
        str(paths.project_root),
        "--expected-master-sha256",
        report["master_sha256_after"],
        "--expected-rows",
        str(report["rows_after"]),
        "--json",
    ]
    sync_result = executor(sync_command, paths.project_root, options.timeout_seconds)
    if sync_result.returncode != 0 or sync_result.timed_out:
        report["sidecar_sync_status"] = "failed"
        return command_failure(report, paths.report_path, sync_result, "phase5_sidecar_sync")
    sync_payload = parse_command_json(sync_result)
    report["sidecar_sync_status"] = str(sync_payload.get("status") or "failed")
    if report["sidecar_sync_status"] != "ok":
        report["blockers"].extend(list(sync_payload.get("validation_errors") or []))
        return finish(report, paths.report_path, "blocked", "phase5_sidecar_sync_blocked")
    report["validations_executed"].append("phase5_sidecar_source_alignment")

    if options.run_phase5:
        phase5_result = executor(
            [sys.executable, str(paths.phase5_script)],
            paths.project_root,
            options.timeout_seconds,
        )
        if phase5_result.returncode != 0 or phase5_result.timed_out:
            report["phase5_status"] = "failed"
            return command_failure(report, paths.report_path, phase5_result, "phase5_rebuild")
        phase5_payload = parse_command_json(phase5_result)
        report["phase5_status"] = str(phase5_payload.get("status") or "failed")
        if report["phase5_status"] != "ok":
            return finish(report, paths.report_path, "blocked", "phase5_rebuild_blocked")
        trade_enriched = paths.project_root / "data" / "features" / "trade_enriched.parquet"
        training_dataset = paths.project_root / "data" / "features" / "training_dataset.parquet"
        report["trade_enriched_rows"] = (
            int(len(pd.read_parquet(trade_enriched))) if trade_enriched.exists() else None
        )
        report["training_dataset_rows"] = (
            int(len(pd.read_parquet(training_dataset))) if training_dataset.exists() else None
        )
        report["validations_executed"].append("optional_phase5_rebuild")
    else:
        report["phase5_status"] = "not_requested"

    return finish(report, paths.report_path, "ok", "official_import_and_alignment_ok")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paper/shadow-only Bitradex OCR V1.1 ingestion workflow."
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--input-dir", default=r"E:\bitradex\Bitradex prints")
    parser.add_argument("--package-dir")
    parser.add_argument("--report")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    mode.add_argument("--apply-import", action="store_true", help="Apply through the official importer.")
    parser.add_argument("--run-phase5", action="store_true", help="Opt in to Phase5 after import.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = resolve_paths(args.project_root, args.input_dir, args.package_dir, args.report)
    options = OrchestratorOptions(
        paths=paths,
        apply_import=bool(args.apply_import),
        run_phase5=bool(args.run_phase5),
        timeout_seconds=max(1, int(args.timeout_seconds)),
        lang=str(args.lang),
    )
    report = run_ingestion(options)
    output = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(output if args.json else json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return {"ok": 0, "blocked": 1, "failed": 2}.get(str(report["status"]), 2)


if __name__ == "__main__":
    raise SystemExit(main())
