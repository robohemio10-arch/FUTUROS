from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_VOLUME_NAME = "futuros_freqtrade_paper_db"
DEFAULT_VOLUME_DB_PATH = "/paper-db/tradesv3.paper.sqlite"
DEFAULT_OUTPUT = Path("data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite")
DEFAULT_REPORT = Path("data/reports/freqtrade_paper_db_snapshot_export.json")
DEFAULT_DOCKER_IMAGE = "python:3.12-alpine"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def export_local_sqlite_snapshot(source_db: str | Path, output: str | Path) -> dict[str, Any]:
    source = Path(source_db)
    target = Path(output)
    if not source.exists():
        return snapshot_report(
            status="missing_source",
            reason="source_db_missing",
            source=str(source),
            output=str(target),
        )

    ensure_parent(target)
    temp_target = target.with_suffix(target.suffix + ".tmp")
    if temp_target.exists():
        temp_target.unlink()

    source_uri = source.resolve().as_uri()
    src = None
    dst = None
    try:
        src = sqlite3.connect(f"{source_uri}?mode=ro", uri=True, timeout=30)
        dst = sqlite3.connect(str(temp_target), timeout=30)
        src.backup(dst)
        dst.close()
        src.close()
        dst = None
        src = None
        temp_target.replace(target)
    except Exception as exc:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        if temp_target.exists():
            temp_target.unlink()
        return snapshot_report(
            status="blocked",
            reason="sqlite_backup_failed",
            source=str(source),
            output=str(target),
            error=repr(exc),
        )

    return snapshot_report(
        status="ok",
        reason=None,
        source=str(source),
        output=str(target),
        output_size_bytes=target.stat().st_size if target.exists() else None,
    )


def build_docker_export_command(
    *,
    volume_name: str,
    output: str | Path,
    docker_image: str,
    volume_db_path: str,
) -> list[str]:
    target = Path(output)
    output_dir = target.parent.resolve()
    output_name = target.name
    inline = f"""
import json
import sqlite3
import sys
from pathlib import Path

source = Path({volume_db_path!r})
target = Path('/snapshot') / {output_name!r}
tmp = target.with_suffix(target.suffix + '.tmp')
target.parent.mkdir(parents=True, exist_ok=True)
if tmp.exists():
    tmp.unlink()
src = sqlite3.connect(f'{{source.as_uri()}}?mode=ro', uri=True, timeout=30)
dst = sqlite3.connect(str(tmp), timeout=30)
src.backup(dst)
dst.close()
src.close()
tmp.replace(target)
print(json.dumps({{'status': 'ok', 'output': str(target), 'size_bytes': target.stat().st_size}}, sort_keys=True))
"""
    return [
        "docker",
        "run",
        "--rm",
        "--mount",
        f"type=volume,source={volume_name},target=/paper-db,readonly",
        "--mount",
        f"type=bind,source={output_dir},target=/snapshot",
        docker_image,
        "python",
        "-c",
        inline,
    ]


def export_docker_volume_snapshot(
    *,
    volume_name: str = DEFAULT_VOLUME_NAME,
    output: str | Path = DEFAULT_OUTPUT,
    report_path: str | Path = DEFAULT_REPORT,
    docker_image: str = DEFAULT_DOCKER_IMAGE,
    volume_db_path: str = DEFAULT_VOLUME_DB_PATH,
) -> dict[str, Any]:
    target = Path(output)
    ensure_parent(target)
    command = build_docker_export_command(
        volume_name=volume_name,
        output=target,
        docker_image=docker_image,
        volume_db_path=volume_db_path,
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
    except Exception as exc:
        payload = snapshot_report(
            status="blocked",
            reason="docker_snapshot_export_failed",
            source=f"docker-volume:{volume_name}:{volume_db_path}",
            output=str(target),
            error=repr(exc),
        )
        write_json(Path(report_path), payload)
        return payload

    payload = snapshot_report(
        status="ok" if completed.returncode == 0 and target.exists() else "blocked",
        reason=None if completed.returncode == 0 and target.exists() else "docker_snapshot_export_failed",
        source=f"docker-volume:{volume_name}:{volume_db_path}",
        output=str(target),
        output_size_bytes=target.stat().st_size if target.exists() else None,
        docker_returncode=completed.returncode,
        docker_stdout=completed.stdout.strip()[:4000],
        docker_stderr=completed.stderr.strip()[:4000],
    )
    write_json(Path(report_path), payload)
    return payload


def snapshot_report(
    *,
    status: str,
    reason: str | None,
    source: str,
    output: str,
    output_size_bytes: int | None = None,
    error: str | None = None,
    docker_returncode: int | None = None,
    docker_stdout: str | None = None,
    docker_stderr: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "source": source,
        "output": output,
        "output_size_bytes": output_size_bytes,
        "error": error,
        "docker_returncode": docker_returncode,
        "docker_stdout": docker_stdout,
        "docker_stderr": docker_stderr,
        "paper_only": True,
        "live_trading_enabled": False,
        "order_submission_enabled": False,
        "real_order_submission_enabled": False,
        "exchange_private_access": False,
        "created_at": utc_now(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a read-only snapshot of the Freqtrade paper SQLite named volume.")
    parser.add_argument("--volume-name", default=DEFAULT_VOLUME_NAME)
    parser.add_argument("--volume-db-path", default=DEFAULT_VOLUME_DB_PATH)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--local-source-db", default=None, help="Test/diagnostic mode: backup a local SQLite file without Docker.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.local_source_db:
        payload = export_local_sqlite_snapshot(args.local_source_db, args.output)
        write_json(Path(args.report), payload)
    else:
        payload = export_docker_volume_snapshot(
            volume_name=args.volume_name,
            output=args.output,
            report_path=args.report,
            docker_image=args.docker_image,
            volume_db_path=args.volume_db_path,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
