"""Runtime-report persistence for the post-OCR official baseline."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd


BASELINE_JSON = "OFFICIAL_TRADES_MASTER_BASELINE_V1.json"
BASELINE_MD = "OFFICIAL_TRADES_MASTER_BASELINE_V1.md"
BY_SYMBOL_SIDE_CSV = "OFFICIAL_TRADES_MASTER_BY_SYMBOL_SIDE_V1.csv"
BY_MONTH_CSV = "OFFICIAL_TRADES_MASTER_BY_MONTH_V1.csv"
BY_HOUR_CSV = "OFFICIAL_TRADES_MASTER_BY_HOUR_V1.csv"
BY_DURATION_CSV = "OFFICIAL_TRADES_MASTER_BY_DURATION_V1.csv"
SHA256_MANIFEST_CSV = "SHA256_MANIFEST.csv"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(
    path: Path,
    data: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temp_name,
            path,
        )
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_text(
    path: Path,
    text: str,
) -> None:
    _atomic_write_bytes(
        path,
        text.encode("utf-8"),
    )


def _csv_text(
    rows: list[dict[str, Any]],
) -> str:
    frame = pd.DataFrame(rows)
    return frame.to_csv(
        index=False,
        lineterminator="\n",
    )


def _render_markdown(
    payload: dict[str, Any],
) -> str:
    metrics = payload["global_metrics"]
    opening = payload["opening_time"]

    lines = [
        "# Official Trades Master Baseline V1",
        "",
        f"- Engineering status: `{payload['engineering_status']}`",
        f"- Baseline status: `{payload['official_baseline_status']}`",
        f"- Quant edge status: `{payload['quant_edge_status']}`",
        f"- Master SHA256: `{payload['master_sha256']}`",
        f"- Rows: `{payload['rows']}`",
        f"- Columns: `{payload['columns']}`",
        "",
        "## Economic baseline",
        "",
        f"- Reported PnL: `{metrics['reported_pnl_total']:.6f}` USDT",
        (
            "- Economic fee adjustment: "
            f"`{metrics['economic_fee_adjustment_total']:.6f}` USDT"
        ),
        (
            "- Economic net PnL: "
            f"`{metrics['economic_net_pnl_total']:.6f}` USDT"
        ),
        f"- Profit Factor: `{metrics['profit_factor']:.10f}`",
        f"- Win rate: `{metrics['win_rate'] * 100:.6f}%`",
        (
            "- Max drawdown: "
            f"`{metrics['max_drawdown_signed']:.6f}` USDT"
        ),
        f"- Expectancy mean: `{metrics['expectancy_mean']:.12f}` USDT/trade",
        "",
        "## Temporal eligibility",
        "",
        f"- Opening time available: `{opening['available']}`",
        f"- Opening time unavailable: `{opening['unavailable']}`",
        f"- Legacy sentinel: `{opening['legacy_sentinel']}`",
        "",
        "## Safety",
        "",
        "- PAPER / SHADOW / RESEARCH ONLY",
        "- Writes official master: `false`",
        "- Sends orders: `false`",
        "- Changes risk: `false`",
        "- Operational authority: `false`",
        "",
    ]

    return "\n".join(lines)


def persist_baseline_reports(
    payload: dict[str, Any],
    *,
    output_dir: str | Path,
) -> dict[str, str]:
    target_dir = Path(output_dir)

    files: dict[str, bytes] = {
        BASELINE_JSON: (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
        BASELINE_MD: _render_markdown(payload).encode("utf-8"),
        BY_SYMBOL_SIDE_CSV: _csv_text(
            payload["by_symbol_side"]
        ).encode("utf-8"),
        BY_MONTH_CSV: _csv_text(
            payload["by_month"]
        ).encode("utf-8"),
        BY_HOUR_CSV: _csv_text(
            payload["by_close_hour_utc"]
        ).encode("utf-8"),
        BY_DURATION_CSV: _csv_text(
            payload["by_duration_bucket"]
        ).encode("utf-8"),
    }

    written: dict[str, str] = {}

    for name, content in files.items():
        path = target_dir / name
        _atomic_write_bytes(
            path,
            content,
        )
        written[name] = str(path)

    manifest_rows: list[dict[str, Any]] = []

    for name in sorted(files):
        path = target_dir / name
        manifest_rows.append(
            {
                "file": name,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    manifest_content = _csv_text(
        manifest_rows
    ).encode("utf-8")

    manifest_path = (
        target_dir / SHA256_MANIFEST_CSV
    )

    _atomic_write_bytes(
        manifest_path,
        manifest_content,
    )

    written[SHA256_MANIFEST_CSV] = str(
        manifest_path
    )

    return written


def report_content_sha256(
    payload: dict[str, Any],
) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256_bytes(serialized)


__all__ = [
    "BASELINE_JSON",
    "BASELINE_MD",
    "BY_DURATION_CSV",
    "BY_HOUR_CSV",
    "BY_MONTH_CSV",
    "BY_SYMBOL_SIDE_CSV",
    "SHA256_MANIFEST_CSV",
    "persist_baseline_reports",
    "report_content_sha256",
]
