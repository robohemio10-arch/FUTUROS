from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts import run_backup_snapshot as backup_cli
from scripts import run_restore_dry_run as restore_cli
from scripts import run_system_healthcheck as healthcheck_cli
from smartcrypto.ops.backup_restore import create_backup_snapshot, run_restore_dry_run
from smartcrypto.ops.system_healthcheck import run_system_healthcheck

NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def safe_flags() -> dict[str, object]:
    return {
        "paper_only": True,
        "shadow_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "sends_orders": False,
        "changes_risk": False,
    }


def paths(tmp_path: Path) -> dict[str, Path]:
    reports = tmp_path / "reports"
    return {
        "readiness_report": reports / "readiness_gate_report.json",
        "paper_soak_report": reports / "paper_soak_report.json",
        "critical_alerting_report": reports / "critical_alerting_report.json",
        "risk_recovery_report": reports / "risk_recovery_mode_audit_report.json",
        "market_health_report": reports / "market_data_health_audit_report.json",
        "state_reconciliation_report": reports / "state_reconciliation_audit_report.json",
        "ledger_report": reports / "order_intent_capital_ledger_audit_report.json",
        "backup_report": reports / "backup_snapshot_report.json",
        "restore_report": reports / "restore_dry_run_report.json",
        "healthcheck_report": reports / "system_healthcheck_report.json",
        "dockerfile": tmp_path / "Dockerfile",
        "compose_file": tmp_path / "docker-compose.paper.yml",
    }


def write_clean_health_sources(tmp_path: Path, *, overrides: dict[str, dict] | None = None, docker_healthcheck: bool = True) -> dict[str, Path]:
    selected = paths(tmp_path)
    overrides = overrides or {}
    payloads = {
        "readiness_report": {"status": "ok", "readiness_approved": True, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "paper_soak_report": {"status": "ok", "soak_days": 8, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "critical_alerting_report": {"status": "ok", "critical_alerts": 0, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "risk_recovery_report": {"status": "ok", "recommended_mode": "NORMAL", "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "market_health_report": {"status": "ok", "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "state_reconciliation_report": {"status": "ok", "reconciliation_required": False, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "ledger_report": {"status": "ok", "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "backup_report": {"status": "ok", "file_count": 2, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
        "restore_report": {"status": "ok", "dry_run": True, "generated_at_utc": "2026-06-03T12:00:00Z", **safe_flags()},
    }
    for name, patch in overrides.items():
        payloads[name] = {**payloads[name], **patch}
    for name, payload in payloads.items():
        write_json(selected[name], payload)
    selected["dockerfile"].write_text("FROM python:3.12\nHEALTHCHECK CMD python -V\n" if docker_healthcheck else "FROM python:3.12\n", encoding="utf-8")
    selected["compose_file"].write_text("services:\n  app:\n    image: smartcrypto\n", encoding="utf-8")
    return selected


def run_clean_healthcheck(tmp_path: Path, *, overrides: dict[str, dict] | None = None, strict: bool = False, docker_healthcheck: bool = True) -> dict:
    selected = write_clean_health_sources(tmp_path, overrides=overrides, docker_healthcheck=docker_healthcheck)
    return run_system_healthcheck(
        readiness_report=selected["readiness_report"],
        paper_soak_report=selected["paper_soak_report"],
        critical_alerting_report=selected["critical_alerting_report"],
        risk_recovery_report=selected["risk_recovery_report"],
        market_health_report=selected["market_health_report"],
        state_reconciliation_report=selected["state_reconciliation_report"],
        ledger_report=selected["ledger_report"],
        backup_report=selected["backup_report"],
        restore_report=selected["restore_report"],
        dockerfile=selected["dockerfile"],
        compose_file=selected["compose_file"],
        report_path=selected["healthcheck_report"],
        max_report_age_seconds=3600,
        strict=strict,
        now=NOW,
    )


def test_system_healthcheck_accepts_clean_paper_shadow_environment(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path)
    assert report["status"] == "ok"
    assert report["checks"]["docker_healthcheck"]["status"] == "ok"
    assert report["paper_only"] is True
    assert report["live_trading_enabled"] is False


def test_system_healthcheck_blocks_live_flags(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path, overrides={"readiness_report": {"live_trading_enabled": True}})
    assert report["status"] == "blocked"
    assert "unsafe_source_safety_flag:readiness_report:live_trading_enabled" in report["blocking_findings"]


def test_system_healthcheck_blocks_order_submission_flags(tmp_path: Path) -> None:
    report = run_system_healthcheck(
        readiness_report=None,
        paper_soak_report=None,
        critical_alerting_report=None,
        risk_recovery_report=None,
        market_health_report=None,
        state_reconciliation_report=None,
        ledger_report=None,
        backup_report=None,
        restore_report=None,
        dockerfile=None,
        compose_file=None,
        report_path=tmp_path / "report.json",
        safety_overrides={"order_submission_enabled": True, "real_order_submission_enabled": True},
        now=NOW,
    )
    assert report["status"] == "blocked"
    assert "unsafe_safety_flag:order_submission_enabled" in report["blocking_findings"]
    assert "unsafe_safety_flag:real_order_submission_enabled" in report["blocking_findings"]


def test_system_healthcheck_blocks_missing_required_reports_in_strict_mode(tmp_path: Path) -> None:
    selected = write_clean_health_sources(tmp_path)
    selected["readiness_report"].unlink()
    report = run_system_healthcheck(
        readiness_report=selected["readiness_report"],
        paper_soak_report=selected["paper_soak_report"],
        critical_alerting_report=selected["critical_alerting_report"],
        risk_recovery_report=selected["risk_recovery_report"],
        market_health_report=selected["market_health_report"],
        state_reconciliation_report=selected["state_reconciliation_report"],
        ledger_report=selected["ledger_report"],
        backup_report=selected["backup_report"],
        restore_report=selected["restore_report"],
        dockerfile=selected["dockerfile"],
        compose_file=selected["compose_file"],
        report_path=selected["healthcheck_report"],
        strict=True,
        now=NOW,
    )
    assert report["status"] == "blocked"
    assert "missing_required_report:readiness_report" in report["blocking_findings"]


def test_system_healthcheck_blocks_blocked_readiness_gate(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path, overrides={"readiness_report": {"status": "blocked", "readiness_approved": False}})
    assert report["status"] == "blocked"
    assert "readiness_gate_blocked" in report["blocking_findings"]


def test_system_healthcheck_blocks_blocked_critical_alerting(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path, overrides={"critical_alerting_report": {"status": "blocked", "critical_alerts": 1}})
    assert report["status"] == "blocked"
    assert "critical_alerting_blocked" in report["blocking_findings"]


def test_system_healthcheck_blocks_risk_panic_or_reconciling(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path, overrides={"risk_recovery_report": {"recommended_mode": "PANIC"}})
    assert report["status"] == "blocked"
    assert "risk_recovery_mode_panic" in report["blocking_findings"]


def test_system_healthcheck_reports_missing_docker_healthcheck(tmp_path: Path) -> None:
    report = run_clean_healthcheck(tmp_path, docker_healthcheck=False)
    assert report["status"] == "warning"
    assert report["checks"]["docker_healthcheck"]["status"] == "warning"
    assert "missing_docker_healthcheck" in report["warnings"]


def test_backup_snapshot_writes_manifest_and_checksums(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "a.txt").parent.mkdir(parents=True)
    (source / "a.txt").write_text("alpha", encoding="utf-8")
    report = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup", report_path=tmp_path / "backup_report.json", now=NOW)
    manifest_path = Path(report["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["file_count"] == 1
    assert manifest["files"][0]["sha256"]
    assert (tmp_path / "backup" / "source" / "a.txt").exists()


def test_backup_snapshot_blocks_sensitive_env_or_secret_files(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secret_file = tmp_path / "api_secret.txt"
    env_file.write_text("TOKEN=real", encoding="utf-8")
    secret_file.write_text("secret", encoding="utf-8")
    report = create_backup_snapshot(inputs=[env_file, secret_file], output_dir=tmp_path / "backup", report_path=tmp_path / "report.json")
    assert report["status"] == "blocked"
    assert "sensitive_file_blocked" in report["reason"]
    assert report["write_performed"] is False


def test_backup_snapshot_blocks_freqtrade_db_by_default(tmp_path: Path) -> None:
    db = tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite"
    db.parent.mkdir(parents=True)
    db.write_text("sqlite", encoding="utf-8")
    report = create_backup_snapshot(inputs=[db], output_dir=tmp_path / "backup", report_path=tmp_path / "report.json")
    assert report["status"] == "blocked"
    assert "freqtrade_db_blocked" in report["reason"]


def test_backup_snapshot_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "b.txt").write_text("beta", encoding="utf-8")
    first = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup1", report_path=tmp_path / "report1.json", now=NOW)
    second = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup2", report_path=tmp_path / "report2.json", now=NOW)
    assert [(item["relative_path"], item["sha256"], item["size_bytes"]) for item in first["files"]] == [
        (item["relative_path"], item["sha256"], item["size_bytes"]) for item in second["files"]
    ]


def test_restore_dry_run_validates_manifest_and_hashes(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("restore-me", encoding="utf-8")
    backup = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup", report_path=tmp_path / "backup_report.json", now=NOW)
    report = run_restore_dry_run(backup_dir=backup["backup_dir"], manifest=backup["manifest_path"], report_path=tmp_path / "restore_report.json", now=NOW)
    assert report["status"] == "ok"
    assert report["manifest_valid"] is True
    assert report["file_count"] == 1
    assert report["write_performed"] is False


def test_restore_dry_run_never_overwrites_real_files(tmp_path: Path) -> None:
    source = tmp_path / "target.txt"
    source.write_text("original", encoding="utf-8")
    backup = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup", report_path=tmp_path / "backup_report.json", now=NOW)
    source.write_text("changed-after-backup", encoding="utf-8")
    report = run_restore_dry_run(backup_dir=backup["backup_dir"], manifest=backup["manifest_path"], report_path=tmp_path / "restore_report.json", now=NOW)
    assert report["status"] == "ok"
    assert source.read_text(encoding="utf-8") == "changed-after-backup"
    assert report["write_performed"] is False


def test_cli_run_system_healthcheck_runs_successfully(tmp_path: Path, capsys) -> None:
    selected = write_clean_health_sources(tmp_path)
    rc = healthcheck_cli.main(
        [
            "--readiness-report",
            str(selected["readiness_report"]),
            "--paper-soak-report",
            str(selected["paper_soak_report"]),
            "--critical-alerting-report",
            str(selected["critical_alerting_report"]),
            "--risk-recovery-report",
            str(selected["risk_recovery_report"]),
            "--market-health-report",
            str(selected["market_health_report"]),
            "--state-reconciliation-report",
            str(selected["state_reconciliation_report"]),
            "--ledger-report",
            str(selected["ledger_report"]),
            "--backup-report",
            str(selected["backup_report"]),
            "--restore-report",
            str(selected["restore_report"]),
            "--dockerfile",
            str(selected["dockerfile"]),
            "--compose-file",
            str(selected["compose_file"]),
            "--report",
            str(selected["healthcheck_report"]),
            "--max-report-age-seconds",
            "999999999",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"


def test_cli_run_backup_snapshot_runs_successfully(tmp_path: Path, capsys) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")
    rc = backup_cli.main(["--inputs", str(source), "--output-dir", str(tmp_path / "backup"), "--report", str(tmp_path / "report.json")])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"
    assert Path(output["manifest_path"]).exists()


def test_cli_run_restore_dry_run_runs_successfully(tmp_path: Path, capsys) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")
    backup = create_backup_snapshot(inputs=[source], output_dir=tmp_path / "backup", report_path=tmp_path / "backup_report.json", now=NOW)
    rc = restore_cli.main(["--backup-dir", backup["backup_dir"], "--manifest", backup["manifest_path"], "--report", str(tmp_path / "restore_report.json")])
    output = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert output["status"] == "ok"


def test_does_not_touch_training_dataset_or_trades_master(tmp_path: Path) -> None:
    protected = [
        tmp_path / "data" / "features" / "training_dataset.parquet",
        tmp_path / "data" / "trades" / "trades_master.xlsx",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    run_clean_healthcheck(tmp_path)
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_does_not_touch_freqtrade_db_registry_models_signal_producer_or_config(tmp_path: Path) -> None:
    protected = [
        tmp_path / "freqtrade" / "user_data" / "tradesv3.paper.sqlite",
        tmp_path / "data" / "models" / "registry" / "model_registry.json",
        tmp_path / "data" / "models" / "shadow" / "model.joblib",
        tmp_path / "data" / "runtime" / "active_freqtrade_signals.json",
        tmp_path / ".env",
    ]
    for path in protected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    run_clean_healthcheck(tmp_path)
    assert all(path.read_text(encoding="utf-8") == "sentinel" for path in protected)


def test_never_sends_orders_or_accesses_exchange() -> None:
    checked = [
        Path("smartcrypto/ops/system_healthcheck.py"),
        Path("smartcrypto/ops/backup_restore.py"),
        Path("scripts/run_system_healthcheck.py"),
        Path("scripts/run_backup_snapshot.py"),
        Path("scripts/run_restore_dry_run.py"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in checked)
    forbidden = ["create_order", "fetch_balance", "private_get", "freqtradeapi", "ccxt.", "requests.post"]
    assert not any(token in combined for token in forbidden)
