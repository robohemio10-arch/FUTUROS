from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.run_bitradex_ocr_v11_single_command_ingestion import (
    APPLY_SUMMARY_NAME,
    OFFICIAL_COLUMNS,
    POST_IMPORT_AUDIT_NAME,
    CommandResult,
    OrchestratorOptions,
    discover_images,
    parse_args,
    resolve_paths,
    run_ingestion,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_bitradex_ocr_v11_single_command_ingestion.py"
WRAPPER = ROOT / "scripts" / "RUN_BITRADEX_OCR_V11_SINGLE_COMMAND_INGESTION.ps1"


def trade_row(order_id: str, source: str = "image.png") -> dict[str, str]:
    return {
        "moeda": "BTCUSDT",
        "fechar_side": "long",
        "leverage": "10",
        "order_id": order_id,
        "pnl_fechado": "1.25",
        "taxa_lucros_perdas_fechados_pct": "0.5",
        "preco_abertura": "100000",
        "preco_fechamento": "100100",
        "volume_posicao": "0.01",
        "volume_fechado": "0.01",
        "horario_abertura": "2026-06-20 10:00:00",
        "horario_fechamento": "2026-06-20 10:05:00",
        "taxa_1": "0.01",
        "preco_transacao": "100100",
        "volume_transacao": "0.01",
        "direcao_liquidez": "maker",
        "taxa_2": "0.01",
        "horario_transacao": "2026-06-20 10:05:00",
        "source_file": source,
        "imported_at": "2026-06-22T12:00:00Z",
        "_dedup_key": f"dedup-{order_id}",
        "_relaxed_dedup_key": f"relaxed-{order_id}",
        "exchange_source": "bitradex",
        "market_data_source": "binance",
        "ocr_source": "bitradex_ocr_candidate_v1_1",
    }


def write_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def prepare_project(
    tmp_path: Path,
    *,
    images: tuple[str, ...] = ("b.JPG", "a.png"),
    candidate: bool = True,
) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    input_dir = tmp_path / "input"
    package = project / "data" / "staging" / "bitradex_ocr_v11_next_lot"
    input_dir.mkdir(parents=True)
    package.mkdir(parents=True)
    for name in images:
        (input_dir / name).write_bytes(f"image:{name}".encode())

    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "ocr_bitradex_images_to_review.py",
        "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py",
        "sync_ocr_master_v11_phase5_sidecars.py",
        "rebuild_phase5_datasets.py",
    ):
        (scripts / name).write_text("# fixture\n", encoding="utf-8")

    master_rows = [trade_row("aaaaaaaaaaaaaaaaaaaaaaaa", "master.png")]
    write_xlsx(project / "data" / "trades" / "trades_master.xlsx", master_rows)
    pd.DataFrame(master_rows).to_parquet(
        project / "data" / "trades" / "trades_master.parquet",
        index=False,
    )
    if candidate:
        write_xlsx(
            package / "BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx",
            [trade_row("bbbbbbbbbbbbbbbbbbbbbbbb", "a.png")],
        )
    return project, input_dir, package


class FakeExecutor:
    def __init__(
        self,
        *,
        git_dirty: bool = False,
        fail_stage: str | None = None,
        timeout_stage: str | None = None,
        backup_created: bool = True,
        mutate_master_on_ocr: bool = False,
    ) -> None:
        self.git_dirty = git_dirty
        self.fail_stage = fail_stage
        self.timeout_stage = timeout_stage
        self.backup_created = backup_created
        self.mutate_master_on_ocr = mutate_master_on_ocr
        self.commands: list[tuple[str, ...]] = []

    @staticmethod
    def stage(argv: tuple[str, ...]) -> str:
        if argv[0] == "git":
            return "git"
        name = Path(argv[1]).name
        if name == "ocr_bitradex_images_to_review.py":
            return "ocr"
        if name == "apply_bitradex_ocr_orderid_synthetic_v5_to_trades_master.py":
            return "apply"
        if name == "sync_ocr_master_v11_phase5_sidecars.py":
            return "sync"
        if name == "rebuild_phase5_datasets.py":
            return "phase5"
        return name

    def __call__(self, argv: Any, cwd: Path, timeout_seconds: int) -> CommandResult:
        del timeout_seconds
        command = tuple(str(value) for value in argv)
        self.commands.append(command)
        stage = self.stage(command)
        if stage == self.timeout_stage:
            return CommandResult(command, 124, "", "timeout", timed_out=True)
        if stage == self.fail_stage:
            return CommandResult(command, 3, "", "controlled failure")
        if stage == "git":
            return CommandResult(command, 0, " M tracked.py" if self.git_dirty else "", "")
        if stage == "ocr":
            if self.mutate_master_on_ocr:
                master = cwd / "data" / "trades" / "trades_master.parquet"
                master.write_bytes(master.read_bytes() + b"unexpected mutation")
            return CommandResult(command, 0, json.dumps({"status": "ok"}), "")
        if stage == "apply":
            return self.apply(command, cwd)
        if stage == "sync":
            return CommandResult(command, 0, json.dumps({"status": "ok"}), "")
        if stage == "phase5":
            features = cwd / "data" / "features"
            features.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"trade_id": ["one", "two"]}).to_parquet(
                features / "trade_enriched.parquet", index=False
            )
            pd.DataFrame({"trade_id": ["one", "two"]}).to_parquet(
                features / "training_dataset.parquet", index=False
            )
            return CommandResult(command, 0, json.dumps({"status": "ok"}), "")
        return CommandResult(command, 0, "{}", "")

    def apply(self, command: tuple[str, ...], cwd: Path) -> CommandResult:
        package = Path(command[command.index("--package-dir") + 1])
        master_path = cwd / "data" / "trades" / "trades_master.xlsx"
        candidate_path = package / "BITRADEX_OCR_PHASE5_IMPORT_READY.xlsx"
        master = pd.read_excel(master_path, dtype=str, keep_default_na=False)
        candidate = pd.read_excel(candidate_path, dtype=str, keep_default_na=False)
        rows_before = len(master)
        combined = pd.concat([master, candidate], ignore_index=True, sort=False)
        backup_dir = cwd / "data" / "backups" / "fixture"
        if self.backup_created:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / "trades_master.xlsx").write_bytes(master_path.read_bytes())
        combined.to_excel(master_path, index=False)
        summary = {
            "status": "ok",
            "reason": "official_apply_completed",
            "rows_before": rows_before,
            "incoming_rows": len(candidate),
            "rows_after": len(combined),
            "expected_rows_after": len(combined),
            "imported_rows": len(candidate),
            "backup_created": self.backup_created,
            "backup_dir": str(backup_dir) if self.backup_created else None,
            "rollback_command": "fixture rollback" if self.backup_created else None,
            "validation_errors": [],
        }
        post = {
            "status": "ok",
            "rows_total": len(combined),
            "imported_rows": len(candidate),
            "duplicate_order_id_rows_after": 0,
            "post_tail_source_match": True,
            "validation_errors": [],
        }
        (package / APPLY_SUMMARY_NAME).write_text(json.dumps(summary), encoding="utf-8")
        (package / POST_IMPORT_AUDIT_NAME).write_text(json.dumps(post), encoding="utf-8")
        return CommandResult(command, 0, json.dumps(summary), "")


def options(
    project: Path,
    input_dir: Path,
    package: Path,
    *,
    apply_import: bool = False,
    run_phase5: bool = False,
    expected_image_count: int | None = None,
    allow_image_count_mismatch: bool = False,
    max_input_images_in_json: int = 20,
    input_images_manifest: Path | None = None,
) -> OrchestratorOptions:
    paths = resolve_paths(
        project,
        input_dir,
        package,
        project / "data" / "reports" / "orchestrator.json",
        input_images_manifest,
    )
    discovered_count = len(discover_images(input_dir))
    return OrchestratorOptions(
        paths=paths,
        apply_import=apply_import,
        run_phase5=run_phase5,
        timeout_seconds=10,
        expected_image_count=(
            expected_image_count if expected_image_count is not None else max(1, discovered_count)
        ),
        allow_image_count_mismatch=allow_image_count_mismatch,
        max_input_images_in_json=max_input_images_in_json,
    )


def command_stages(executor: FakeExecutor) -> list[str]:
    return [executor.stage(command) for command in executor.commands]


def test_default_is_dry_run_and_does_not_write_master(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    master = project / "data" / "trades" / "trades_master.xlsx"
    before = master.read_bytes()
    executor = FakeExecutor()

    report = run_ingestion(options(project, input_dir, package), executor=executor)

    assert report["status"] == "ok"
    assert report["dry_run"] is True
    assert report["import_status"] == "not_run_dry_run"
    assert report["master_unchanged_by_staging"] is True
    assert master.read_bytes() == before
    assert "apply" not in command_stages(executor)


def test_default_expected_count_50_blocks_different_batch_size(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    paths = resolve_paths(
        project,
        input_dir,
        package,
        project / "data" / "reports" / "orchestrator.json",
    )
    executor = FakeExecutor()

    report = run_ingestion(OrchestratorOptions(paths=paths), executor=executor)

    assert report["status"] == "blocked"
    assert report["reason"] == "input_image_count_mismatch"
    assert report["input_image_count"] == 2
    assert report["expected_image_count"] == 50
    assert report["image_count_mismatch_allowed"] is False
    assert executor.commands == []


def test_allow_image_count_mismatch_continues_with_auditable_warning(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()

    report = run_ingestion(
        options(
            project,
            input_dir,
            package,
            expected_image_count=50,
            allow_image_count_mismatch=True,
        ),
        executor=executor,
    )

    assert report["status"] == "ok"
    assert report["image_count_mismatch_allowed"] is True
    assert report["warnings"] == [
        {
            "reason": "input_image_count_mismatch",
            "input_image_count": 2,
            "expected_image_count": 50,
        }
    ]
    assert command_stages(executor) == ["ocr"]


def test_allow_image_count_mismatch_is_rejected_for_apply(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()
    report = run_ingestion(
        options(
            project,
            input_dir,
            package,
            apply_import=True,
            expected_image_count=50,
            allow_image_count_mismatch=True,
        ),
        executor=executor,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert executor.commands == []


def test_input_dir_missing_blocks(tmp_path: Path) -> None:
    project, _, package = prepare_project(tmp_path)
    report = run_ingestion(
        options(project, tmp_path / "missing", package),
        executor=FakeExecutor(),
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "input_dir_not_found"


def test_empty_input_dir_blocks(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path, images=())
    report = run_ingestion(options(project, input_dir, package), executor=FakeExecutor())
    assert report["status"] == "blocked"
    assert report["reason"] == "empty_input_dir"


def test_image_discovery_is_deterministic(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "z.PNG").write_bytes(b"z")
    (tmp_path / "A.jpg").write_bytes(b"a")
    (nested / "b.webp").write_bytes(b"b")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")

    discovered = discover_images(tmp_path)

    assert [path.relative_to(tmp_path).as_posix() for path in discovered] == [
        "A.jpg",
        "nested/b.webp",
        "z.PNG",
    ]


def test_apply_import_requires_clean_worktree(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor(git_dirty=True)

    report = run_ingestion(
        options(project, input_dir, package, apply_import=True),
        executor=executor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert command_stages(executor) == []


def test_missing_official_ocr_stage_script_blocks(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    (project / "scripts" / "ocr_bitradex_images_to_review.py").unlink()

    report = run_ingestion(options(project, input_dir, package), executor=FakeExecutor())

    assert report["status"] == "blocked"
    assert report["reason"] == "missing_official_ocr_stage_script"


def test_preview_only_never_calls_official_import(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()

    report = run_ingestion(options(project, input_dir, package), executor=executor)

    assert report["preview_status"] == "ok"
    assert report["rows_before"] == 1
    assert report["incoming_rows"] == 1
    assert report["expected_rows_after"] == 2
    assert command_stages(executor) == ["ocr"]


def test_exactly_50_images_follow_expected_dry_run_flow(tmp_path: Path) -> None:
    names = ("a.png",) + tuple(f"image_{index:02d}.jpg" for index in range(1, 50))
    project, input_dir, package = prepare_project(tmp_path, images=names)
    executor = FakeExecutor()

    report = run_ingestion(options(project, input_dir, package), executor=executor)

    assert report["status"] == "ok"
    assert report["input_image_count"] == 50
    assert report["expected_image_count"] == 50
    assert report["preview_status"] == "ok"
    assert command_stages(executor) == ["ocr"]


def test_mismatch_never_calls_import_or_phase5_and_preserves_safety(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()

    report = run_ingestion(
        options(
            project,
            input_dir,
            package,
            apply_import=True,
            run_phase5=True,
            expected_image_count=50,
        ),
        executor=executor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert "apply" not in command_stages(executor)
    assert "phase5" not in command_stages(executor)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    assert report["sends_orders"] is False
    assert report["exchange_private_access"] is False


def test_official_import_blocks_if_mandatory_backup_is_missing(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor(backup_created=False)

    report = run_ingestion(
        options(project, input_dir, package, apply_import=True),
        executor=executor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert executor.commands == []


def test_orchestrator_backup_exists_before_official_import(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()

    report = run_ingestion(
        options(project, input_dir, package, apply_import=True),
        executor=executor,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert report["write_performed"] is False
    assert executor.commands == []


def test_run_phase5_is_opt_in(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    without_phase5 = FakeExecutor()
    report = run_ingestion(
        options(project, input_dir, package, apply_import=True),
        executor=without_phase5,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "legacy_master_import_disabled"
    assert "phase5" not in command_stages(without_phase5)

    project2, input_dir2, package2 = prepare_project(tmp_path / "second")
    with_phase5 = FakeExecutor()
    report2 = run_ingestion(
        options(project2, input_dir2, package2, apply_import=True, run_phase5=True),
        executor=with_phase5,
    )
    assert report2["status"] == "blocked"
    assert report2["reason"] == "legacy_master_import_disabled"
    assert "phase5" not in command_stages(with_phase5)


def test_run_phase5_without_apply_import_is_blocked(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()
    report = run_ingestion(
        options(project, input_dir, package, run_phase5=True),
        executor=executor,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == "run_phase5_requires_apply_import"
    assert executor.commands == []


def test_forbidden_promotion_shadow_and_sqlite_cleanup_are_never_called(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    executor = FakeExecutor()
    report = run_ingestion(options(project, input_dir, package), executor=executor)
    commands = "\n".join(" ".join(command) for command in executor.commands).casefold()

    assert report["status"] == "ok"
    assert "quality_gated" not in commands
    assert "ai_shadow" not in commands
    assert "sqlite" not in commands
    assert "freqtrade" not in commands


def test_safety_flags_and_json_contract_are_always_present(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    report = run_ingestion(options(project, input_dir, package), executor=FakeExecutor())
    required = {
        "status",
        "reason",
        "dry_run",
        "apply_import",
        "run_phase5",
        "input_dir",
        "input_image_count",
        "expected_image_count",
        "image_count_mismatch_allowed",
        "input_images_sample",
        "input_images_sample_size",
        "input_images_manifest_path",
        "staging_status",
        "candidate_status",
        "preview_status",
        "import_status",
        "post_import_audit_status",
        "sidecar_sync_status",
        "phase5_status",
        "rows_before",
        "incoming_rows",
        "rows_after",
        "expected_rows_after",
        "backup_path",
        "validations_executed",
        "blockers",
    }
    assert required.issubset(report)
    assert report["paper_only"] is True
    assert report["shadow_only"] is True
    for field in (
        "live_trading_enabled",
        "order_submission_enabled",
        "real_order_submission_enabled",
        "exchange_private_access",
        "sends_orders",
        "changes_risk",
        "changes_model",
    ):
        assert report[field] is False


def test_subprocess_timeout_is_reported_explicitly(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    report = run_ingestion(
        options(project, input_dir, package),
        executor=FakeExecutor(timeout_stage="ocr"),
    )
    assert report["status"] == "failed"
    assert report["reason"] == "ocr_staging_timeout"


def test_subprocess_error_is_reported_explicitly(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    report = run_ingestion(
        options(project, input_dir, package),
        executor=FakeExecutor(fail_stage="ocr"),
    )
    assert report["status"] == "failed"
    assert report["reason"] == "ocr_staging_failed"


def test_ocr_stage_is_blocked_if_it_changes_master(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    report = run_ingestion(
        options(project, input_dir, package),
        executor=FakeExecutor(mutate_master_on_ocr=True),
    )
    assert report["status"] == "failed"
    assert report["reason"] == "ocr_staging_changed_legacy_master"
    assert report["master_unchanged_by_staging"] is False
    assert "apply" not in report["validations_executed"]


def test_missing_candidate_after_official_ocr_stage_blocks(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path, candidate=False)
    report = run_ingestion(options(project, input_dir, package), executor=FakeExecutor())
    assert report["status"] == "blocked"
    assert report["reason"] == "missing_import_ready_candidate"


def test_ocr_v11_master_requires_source_columns_in_candidate(tmp_path: Path) -> None:
    project, input_dir, package = prepare_project(tmp_path)
    raw_master = trade_row("aaaaaaaaaaaaaaaaaaaaaaaa") | {
        column: "source" for column in (
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
    }
    write_xlsx(project / "data" / "trades" / "trades_master.xlsx", [raw_master])
    pd.DataFrame([raw_master]).to_parquet(
        project / "data" / "trades" / "trades_master.parquet",
        index=False,
    )

    report = run_ingestion(options(project, input_dir, package), executor=FakeExecutor())

    assert report["status"] == "blocked"
    assert report["reason"] == "candidate_or_preview_blocked"
    assert any("missing_ocr_v11_source_columns" in blocker for blocker in report["blockers"])


def test_cli_returns_controlled_json_for_missing_input(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(tmp_path),
            "--input-dir",
            str(tmp_path / "missing"),
            "--report",
            str(report_path),
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "input_dir_not_found"
    assert report_path.exists()


def test_cli_stdout_uses_sample_and_writes_full_image_manifest(tmp_path: Path) -> None:
    names = ("a.png",) + tuple(f"batch_{index:03d}.jpg" for index in range(1, 60))
    project, input_dir, package = prepare_project(tmp_path, images=names)
    report_path = project / "data" / "reports" / "orchestrator.json"
    manifest_path = project / "data" / "reports" / "images.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-root",
            str(project),
            "--input-dir",
            str(input_dir),
            "--package-dir",
            str(package),
            "--report",
            str(report_path),
            "--input-images-manifest",
            str(manifest_path),
            "--expected-image-count",
            "50",
            "--max-input-images-in-json",
            "7",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert completed.returncode == 1
    assert payload["reason"] == "input_image_count_mismatch"
    assert "input_images" not in payload
    assert payload["input_image_count"] == 60
    assert payload["input_images_sample_size"] == 7
    assert len(payload["input_images_sample"]) == 7
    assert payload["input_images_manifest_path"] == str(manifest_path)
    assert manifest["input_image_count"] == 60
    assert len(manifest["input_images"]) == 60


def test_cli_defaults_to_dry_run_and_phase5_false() -> None:
    args = parse_args([])
    assert args.apply_import is False
    assert args.run_phase5 is False
    assert args.expected_image_count == 50
    assert args.allow_image_count_mismatch is False
    assert args.max_input_images_in_json == 20


def test_wrapper_only_forwards_safe_cli_arguments() -> None:
    content = WRAPPER.read_text(encoding="utf-8")
    assert "run_bitradex_ocr_v11_single_command_ingestion.py" in content
    assert "--dry-run" in content
    assert "--apply-import" in content
    assert "--run-phase5" in content
    assert "--expected-image-count" in content
    assert "--allow-image-count-mismatch" in content
    assert "--max-input-images-in-json" in content
    assert "Invoke-Expression" not in content
    assert "trades_master.xlsx" not in content
    assert "ORDER_SUBMISSION_ENABLED" not in content
    assert "REAL_ORDER_SUBMISSION_ENABLED" not in content
    assert set(trade_row("b" * 24)) == set(OFFICIAL_COLUMNS)
