from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


OK = "OK"
WARNING = "WARNING"
BLOCKED = "BLOCKED"


class OutcomeSidecarError(ValueError):
    pass


@dataclass(frozen=True)
class OutcomeSidecarReport:
    status: str
    input_path: str
    output_path: str
    rows: int
    columns: int
    id_column: str
    target_column: str
    outcome_columns_present: list[str]
    outcome_columns_missing: list[str]
    duplicate_ids: int
    null_ids: int
    null_targets: int
    created_at: str = field(default_factory=lambda: utc_now())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_outcome_sidecar(
    frame: pd.DataFrame,
    *,
    input_path: str | Path,
    output_path: str | Path,
    id_column: str = "trade_id",
    target_column: str = "target_win",
    return_column: str = "return_pct",
    mfe_column: str = "mfe_pct",
    mae_column: str = "mae_pct",
    symbol_column: str = "symbol",
    time_column: str = "open_1m_ts",
) -> tuple[pd.DataFrame, OutcomeSidecarReport]:
    if not isinstance(frame, pd.DataFrame):
        raise OutcomeSidecarError("outcome_sidecar_input_must_be_dataframe")
    if id_column not in frame.columns:
        raise OutcomeSidecarError(f"id_column_missing:{id_column}")
    if target_column not in frame.columns:
        raise OutcomeSidecarError(f"target_column_missing:{target_column}")

    null_ids = int(frame[id_column].isna().sum())
    duplicate_ids = int(frame[id_column].duplicated(keep=False).sum())
    null_targets = int(frame[target_column].isna().sum())

    if null_ids:
        raise OutcomeSidecarError(f"id_column_contains_nulls:{null_ids}")
    if duplicate_ids:
        raise OutcomeSidecarError(f"id_column_contains_duplicates:{duplicate_ids}")

    requested_optional = [symbol_column, time_column, return_column, mfe_column, mae_column]
    optional_present = [column for column in requested_optional if column in frame.columns]
    outcome_candidates = [return_column, mfe_column, mae_column]
    if "pnl" in frame.columns:
        outcome_candidates.append("pnl")
    outcome_present = [column for column in outcome_candidates if column in frame.columns]
    outcome_missing = [column for column in outcome_candidates if column not in frame.columns]

    selected_columns = unique_preserve_order([id_column, *optional_present, target_column, *outcome_present])
    sidecar = frame.loc[:, selected_columns].copy()
    validate_sidecar_columns(
        sidecar,
        id_column=id_column,
        time_column=time_column,
        allowed_outcome_columns=set(outcome_present),
        symbol_column=symbol_column,
        target_column=target_column,
    )

    status = WARNING if outcome_missing or null_targets else OK
    report = OutcomeSidecarReport(
        status=status,
        input_path=str(input_path),
        output_path=str(output_path),
        rows=int(len(sidecar)),
        columns=int(len(sidecar.columns)),
        id_column=id_column,
        target_column=target_column,
        outcome_columns_present=outcome_present,
        outcome_columns_missing=outcome_missing,
        duplicate_ids=duplicate_ids,
        null_ids=null_ids,
        null_targets=null_targets,
    )
    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, default=str)
    return sidecar, report


def write_sidecar_outputs(
    *,
    sidecar: pd.DataFrame,
    report: OutcomeSidecarReport,
    output_path: str | Path,
    report_path: str | Path,
) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + f".{uuid.uuid4().hex}.tmp")
    sidecar.to_parquet(temp_output, index=False)
    temp_output.replace(output)
    write_json(report_path, report.to_dict())


def validate_sidecar_columns(
    frame: pd.DataFrame,
    *,
    id_column: str,
    time_column: str,
    allowed_outcome_columns: set[str],
    symbol_column: str,
    target_column: str,
) -> None:
    allowed = {id_column, time_column, symbol_column, target_column, *allowed_outcome_columns}
    unexpected = [column for column in frame.columns if column not in allowed]
    if unexpected:
        raise OutcomeSidecarError(f"sidecar_unexpected_columns:{unexpected}")
    feature_like = [
        column
        for column in frame.columns
        if (
            column.startswith("open_1m_")
            or column.startswith("open_5m_")
            or column.startswith("close_1m_")
            or column.startswith("close_5m_")
        )
        and column != time_column
    ]
    if feature_like:
        raise OutcomeSidecarError(f"sidecar_contains_feature_columns:{feature_like}")


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def read_parquet(path: str | Path) -> pd.DataFrame:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"input_dataset_missing:{input_path}")
    return pd.read_parquet(input_path)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
