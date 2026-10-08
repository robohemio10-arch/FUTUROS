"""Checksum-pinned public history and explicit modeled PIT availability for WQ6.

Archive publication/retrieval is not historical reception. Availability below is
a conservative research model of finalized public observations, not proof of an
event-time capture. Funding is the last settled rate, never predicted funding.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import urllib.request
import zipfile
from bisect import bisect_right
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from smartcrypto.research.aibot_parity.market_intelligence_pnl_ablation import (
    SAFETY_FLAGS,
    _load_bundle,
)

SCHEMA = "market_intelligence_pit_source_foundation_v1"
PUBLIC_ROOT = "https://data.binance.vision/data/"
FEATURES = {"BASIS": "mi_spot_perp_basis_bps", "FUNDING": "mi_funding_rate_last_settled"}
AVAILABILITY = {
    "BASIS": "MODELED_PUBLIC_CLOSED_1M_FINALIZATION_PLUS_60S",
    "FUNDING": "MODELED_PUBLIC_FUNDING_SETTLEMENT_PLUS_300S",
}
SHA_PATTERN = r"^[0-9a-f]{64}$"


class PITSourceError(ValueError):
    """History, integrity or publication assumptions cannot be certified."""


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    archive_sha256: str = Field(pattern=SHA_PATTERN)
    row_sha256: str = Field(pattern=SHA_PATTERN)

    @model_validator(mode="after")
    def public_only(self) -> Provenance:
        if not self.url.startswith(PUBLIC_ROOT) or not self.url.endswith(".zip"):
            raise ValueError("unauthorized_public_source")
        return self


class PITObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["market_intelligence_pit_source_foundation_v1"] = (
        "market_intelligence_pit_source_foundation_v1"
    )
    observation_id: str = Field(pattern=SHA_PATTERN)
    family: Literal["BASIS", "FUNDING"]
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    event_time_utc: datetime
    available_at_utc: datetime
    source: Literal["binance_public_synchronized_spot_perp_1m", "binance_public_funding_history"]
    availability_semantics: str
    availability_is_modeled: Literal[True] = True
    value: float = Field(allow_inf_nan=False)
    max_age_seconds: int = Field(gt=0)
    provenance: tuple[Provenance, ...]

    @model_validator(mode="after")
    def contract(self) -> PITObservation:
        for value in (self.event_time_utc, self.available_at_utc):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("timestamp_must_be_utc")
        delay = 60 if self.family == "BASIS" else 300
        expected_source = (
            "binance_public_synchronized_spot_perp_1m"
            if self.family == "BASIS"
            else "binance_public_funding_history"
        )
        if (
            self.source != expected_source
            or self.availability_semantics != AVAILABILITY[self.family]
        ):
            raise ValueError("source_availability_contract_mismatch")
        if self.available_at_utc != self.event_time_utc + timedelta(seconds=delay):
            raise ValueError("availability_delay_mismatch")
        if self.family == "BASIS":
            if self.max_age_seconds != 300 or len(self.provenance) != 2:
                raise ValueError("basis_contract_mismatch")
            if self.event_time_utc.second or self.event_time_utc.microsecond:
                raise ValueError("basis_finalization_not_minute_boundary")
            expected = (
                f"{PUBLIC_ROOT}spot/daily/klines/{self.symbol}/1m/",
                f"{PUBLIC_ROOT}futures/um/daily/klines/{self.symbol}/1m/",
            )
            if any(
                not item.url.startswith(prefix)
                for item, prefix in zip(self.provenance, expected, strict=True)
            ):
                raise ValueError("basis_leg_symbol_or_market_mismatch")
        elif self.max_age_seconds not in {3900, 7500, 14700, 29100} or len(self.provenance) != 1:
            raise ValueError("funding_settlement_interval_invalid")
        elif not self.provenance[0].url.startswith(
            f"{PUBLIC_ROOT}futures/um/monthly/fundingRate/{self.symbol}/"
        ):
            raise ValueError("funding_provenance_symbol_mismatch")
        content = self.model_dump(mode="json", exclude={"observation_id"})
        if self.observation_id != sha256(encode(content)):
            raise ValueError("observation_hash_mismatch")
        return self


def observation(**fields: Any) -> PITObservation:
    content = {
        "schema_version": SCHEMA,
        "availability_is_modeled": True,
        **fields,
    }
    # Use the model's canonical datetime encoding before sealing identity.
    unsealed = PITObservation.model_construct(observation_id="0" * 64, **content)
    payload = unsealed.model_dump(mode="json", exclude={"observation_id"})
    return PITObservation.model_validate({**payload, "observation_id": sha256(encode(payload))})


def external_root(path: Path, project_root: Path) -> Path:
    if not path.is_absolute():
        raise PITSourceError("output_must_be_absolute")
    for parent in (path, *path.parents):
        if parent.is_symlink() or (parent / ".git").exists():
            raise PITSourceError("output_symlink_or_git_worktree")
    root = path.resolve()
    if root.is_relative_to(project_root.resolve()) or "runtime" in {p.lower() for p in root.parts}:
        raise PITSourceError("operational_or_project_output_forbidden")
    return root


def write_once(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise PITSourceError(f"materialization_conflict:{path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # Linking is atomic and cannot replace a concurrent writer's target.
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise PITSourceError(f"materialization_conflict:{path.name}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download(url: str) -> bytes:
    if not url.startswith(PUBLIC_ROOT):
        raise PITSourceError("unauthorized_download")
    request = urllib.request.Request(url, headers={"User-Agent": "smartcrypto-pit-research/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        if not response.url.startswith(PUBLIC_ROOT):
            raise PITSourceError("public_download_redirect_not_authorized")
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000:
        raise PITSourceError("archive_size_limit")
    return raw


def archive_rows(
    relative: str,
    cache_root: Path,
    *,
    allow_download: bool,
    fetch: Callable[[str], bytes] = download,
) -> tuple[list[list[str]], str]:
    if ".." in Path(relative).parts or not relative.endswith(".zip"):
        raise PITSourceError("archive_path_invalid")
    target = cache_root / relative
    if target.is_absolute() and any(parent.is_symlink() for parent in (target, *target.parents)):
        raise PITSourceError("cache_symlink_forbidden")
    checksum_path = target.with_suffix(".zip.CHECKSUM")
    if not target.is_file() or not checksum_path.is_file():
        if not allow_download:
            raise PITSourceError(f"SOURCE_CACHE_MISSING:{relative}")
        checksum = fetch(PUBLIC_ROOT + relative + ".CHECKSUM")
        raw = fetch(PUBLIC_ROOT + relative)
    else:
        if target.is_symlink() or checksum_path.is_symlink():
            raise PITSourceError("cache_symlink_forbidden")
        checksum, raw = checksum_path.read_bytes(), target.read_bytes()
    tokens = checksum.decode("ascii").split()
    if len(tokens) != 2 or tokens[1].lstrip("*") != target.name or tokens[0] != sha256(raw):
        raise PITSourceError(f"archive_checksum_mismatch:{relative}")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or not entries[0].filename.endswith(".csv"):
            raise PITSourceError("archive_csv_ambiguous")
        if entries[0].file_size > 20_000_000:
            raise PITSourceError("archive_expansion_limit")
        rows = list(csv.reader(io.StringIO(archive.read(entries[0]).decode("utf-8-sig"))))
    if allow_download:
        write_once(target, raw)
        write_once(checksum_path, checksum)
    return rows, sha256(raw)


def _klines(rows: list[list[str]], *, spot: bool) -> dict[int, tuple[float, str]]:
    result: dict[int, tuple[float, str]] = {}
    scale = 1_000_000 if spot else 1000
    for row in rows:
        if row and row[0] == "open_time":
            continue
        if len(row) != 12:
            raise PITSourceError("kline_schema_invalid")
        opened, closed = int(row[0]), int(row[6])
        # Normalize the inclusive close timestamp to the exact minute boundary.
        start_ms = opened * 1000 // scale
        if opened % (60 * scale) or closed != opened + 60 * scale - 1:
            raise PITSourceError("kline_interval_not_closed_1m")
        price = float(row[4])
        if not 0 < price < float("inf") or start_ms in result:
            raise PITSourceError("kline_price_or_identity_invalid")
        result[start_ms] = price, sha256(encode(row))
    return result


def basis_observations(
    symbol: str,
    spot_rows: list[list[str]],
    perp_rows: list[list[str]],
    spot_relative: str,
    perp_relative: str,
    spot_hash: str,
    perp_hash: str,
    required_minutes: set[int],
) -> list[PITObservation]:
    spot, perp = _klines(spot_rows, spot=True), _klines(perp_rows, spot=False)
    result: list[PITObservation] = []
    for minute in sorted(required_minutes & spot.keys() & perp.keys()):
        spot_price, spot_row_hash = spot[minute]
        perp_price, perp_row_hash = perp[minute]
        event = datetime.fromtimestamp(minute / 1000, timezone.utc) + timedelta(minutes=1)
        result.append(
            observation(
                family="BASIS",
                symbol=symbol,
                event_time_utc=event,
                available_at_utc=event + timedelta(seconds=60),
                source="binance_public_synchronized_spot_perp_1m",
                availability_semantics=AVAILABILITY["BASIS"],
                max_age_seconds=300,
                value=(perp_price / spot_price - 1.0) * 10_000,
                provenance=(
                    Provenance(
                        url=PUBLIC_ROOT + spot_relative,
                        archive_sha256=spot_hash,
                        row_sha256=spot_row_hash,
                    ),
                    Provenance(
                        url=PUBLIC_ROOT + perp_relative,
                        archive_sha256=perp_hash,
                        row_sha256=perp_row_hash,
                    ),
                ),
            )
        )
    return result


def funding_observations(
    symbol: str,
    rows: list[list[str]],
    relative: str,
    digest: str,
) -> list[PITObservation]:
    if not rows or rows[0] != ["calc_time", "funding_interval_hours", "last_funding_rate"]:
        raise PITSourceError("funding_schema_invalid")
    result = []
    for row in rows[1:]:
        if len(row) != 3 or int(row[1]) not in {1, 2, 4, 8}:
            raise PITSourceError("funding_interval_invalid")
        event = datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc)
        result.append(
            observation(
                family="FUNDING",
                symbol=symbol,
                event_time_utc=event,
                available_at_utc=event + timedelta(seconds=300),
                source="binance_public_funding_history",
                value=float(row[2]),
                availability_semantics=AVAILABILITY["FUNDING"],
                max_age_seconds=int(row[1]) * 3600 + 300,
                provenance=(
                    Provenance(
                        url=PUBLIC_ROOT + relative,
                        archive_sha256=digest,
                        row_sha256=sha256(encode(row)),
                    ),
                ),
            )
        )
    return result


def align_observations(
    dataset: pd.DataFrame,
    observations: Sequence[PITObservation],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[PITObservation]] = {}
    seen: set[tuple[str, str, datetime]] = set()
    for record in observations:
        identity = record.symbol, record.family, record.event_time_utc
        if identity in seen:
            raise PITSourceError("pit_observation_identity_collision")
        seen.add(identity)
        grouped.setdefault((record.symbol, record.family), []).append(record)
    index = {}
    for key, items in grouped.items():
        items.sort(key=lambda item: item.available_at_utc)
        index[key] = [item.available_at_utc for item in items], items
    enriched = dataset.copy()
    values: dict[str, list[float | None]] = {name: [] for name in FEATURES.values()}
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for row in dataset.itertuples(index=False):
        decision = pd.Timestamp(row.open_time_utc).to_pydatetime()
        if pd.Timestamp(row.feature_cutoff_utc) > pd.Timestamp(row.open_time_utc):
            raise PITSourceError("baseline_feature_after_decision")
        for family, feature in FEATURES.items():
            times, items = index.get((str(row.symbol), family), ([], []))
            position = bisect_right(times, decision) - 1
            item = items[position] if position >= 0 else None
            status = "UNAVAILABLE" if item is None else "AVAILABLE"
            if (
                item is not None
                and (decision - item.event_time_utc).total_seconds() > item.max_age_seconds
            ):
                status = "STALE"
            if item is not None and (
                item.event_time_utc > decision or item.available_at_utc > decision
            ):
                raise PITSourceError("future_join_blocked")
            values[feature].append(item.value if status == "AVAILABLE" and item else None)
            counters = counts.setdefault(family, {}).setdefault(
                str(row.symbol),
                {
                    "AVAILABLE": 0,
                    "STALE": 0,
                    "UNAVAILABLE": 0,
                },
            )
            counters[status] += 1
    for name, numbers in values.items():
        enriched[name] = numbers
    coverage: dict[str, Any] = {}
    for family in FEATURES:
        segments = {}
        for symbol, counters in counts.get(family, {}).items():
            rows = grouped.get((symbol, family), [])
            total = sum(counters.values())
            segments[symbol] = {
                "source": rows[0].source if rows else "SOURCE_UNAVAILABLE",
                "symbol": symbol,
                "first_event_time": rows[0].event_time_utc.isoformat() if rows else None,
                "last_event_time": rows[-1].event_time_utc.isoformat() if rows else None,
                "rows": len(rows),
                "sha256": sha256(b"".join(encode(r.model_dump(mode="json")) + b"\n" for r in rows)),
                "availability_semantics": AVAILABILITY[family],
                "coverage_count": counters["AVAILABLE"],
                "coverage_pct": counters["AVAILABLE"] * 100 / total if total else 0.0,
                "stale_count": counters["STALE"],
                "missing_count": counters["UNAVAILABLE"],
                "future_join_count": 0,
            }
        available = sum(c["AVAILABLE"] for c in counts.get(family, {}).values())
        coverage[family] = {
            "available_count": available,
            "feature_coverage": available / len(dataset) if len(dataset) else 0.0,
            "stale_or_unavailable_count": len(dataset) - available,
            "future_join_count": 0,
            "by_symbol": segments,
        }
    coverage["FLOW"] = {
        "source_status": "SOURCE_UNAVAILABLE",
        "reason": "no_event_time_flow_capture; archives_do_not_prove_intraminute_availability",
        "available_count": 0,
        "feature_coverage": 0.0,
        "stale_or_unavailable_count": len(dataset),
        "future_join_count": 0,
        "by_symbol": {
            str(symbol): {
                "source": "SOURCE_UNAVAILABLE",
                "symbol": str(symbol),
                "first_event_time": None,
                "last_event_time": None,
                "rows": 0,
                "sha256": None,
                "availability_semantics": "UNAVAILABLE",
                "coverage_count": 0,
                "coverage_pct": 0.0,
                "stale_count": 0,
                "missing_count": int(dataset["symbol"].eq(symbol).sum()),
                "future_join_count": 0,
            }
            for symbol in sorted(dataset["symbol"].unique())
        },
    }
    return enriched, coverage


def build_source_foundation(
    *,
    project_root: Path,
    output_root: Path,
    write: bool = False,
    dataset_path: str | Path | None = None,
    feature_contract_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    split_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    output = external_root(output_root, project_root)
    dataset, _, splits, source = _load_bundle(
        project_root=project_root,
        dataset_path=dataset_path,
        feature_contract_path=feature_contract_path,
        dataset_manifest_path=dataset_manifest_path,
        split_manifest_path=split_manifest_path,
    )
    if set(dataset["symbol"]) - {"BTCUSDT", "ETHUSDT"}:
        raise PITSourceError("unsupported_frozen_symbol")
    # Plan only the short pre-decision candle windows, not the full calendar.
    required: dict[tuple[str, str], set[int]] = {}
    months: set[tuple[str, str]] = set()
    for row in dataset.itertuples(index=False):
        opened = pd.Timestamp(row.open_time_utc)
        for offset in range(2, 6):
            minute = opened.floor("min") - pd.Timedelta(minutes=offset)
            required.setdefault((row.symbol, minute.strftime("%Y-%m-%d")), set()).add(
                int(minute.timestamp() * 1000)
            )
        for time in (opened, opened - pd.Timedelta(days=1)):
            months.add((row.symbol, time.strftime("%Y-%m")))
    requests: set[str] = set()
    for symbol, day in required:
        for market in ("spot", "futures/um"):
            requests.add(f"{market}/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip")
    for symbol, month in months:
        requests.add(f"futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip")

    def obtain(relative: str) -> tuple[str, tuple[list[list[str]], str]]:
        return relative, archive_rows(relative, output / "cache", allow_download=write)

    with ThreadPoolExecutor(max_workers=4) as executor:
        fetched = dict(executor.map(obtain, sorted(requests)))
    records = []
    for (symbol, day), minutes in sorted(required.items()):
        spot = f"spot/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
        perp = f"futures/um/daily/klines/{symbol}/1m/{symbol}-1m-{day}.zip"
        spot_rows, spot_hash = fetched[spot]
        perp_rows, perp_hash = fetched[perp]
        records.extend(
            basis_observations(
                symbol, spot_rows, perp_rows, spot, perp, spot_hash, perp_hash, minutes
            )
        )
    for symbol, month in sorted(months):
        relative = f"futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
        rows, digest = fetched[relative]
        records.extend(funding_observations(symbol, rows, relative, digest))
    records.sort(key=lambda item: (item.family, item.symbol, item.event_time_utc))
    _, coverage = align_observations(dataset, records)
    raw = b"".join(encode(item.model_dump(mode="json")) + b"\n" for item in records)
    report = {
        "schema_version": SCHEMA,
        "status": "ok",
        "source": source,
        "frozen_fold_ids": [split["split_id"] for split in splits],
        "coverage": coverage,
        "availability_is_modeled": True,
        "historical_reception_proven": False,
        "archive_publication_is_not_feature_availability": True,
        "basis_definition": "10000*(closed_perp_price/closed_spot_price-1); exact_same_1m_interval",
        "funding_definition": "last_settled_rate; not_predicted; expires_after_recorded_interval_plus_delay",
        "archives": {
            key: {"url": PUBLIC_ROOT + key, "sha256": value[1]}
            for key, value in sorted(fetched.items())
        },
        "archive_count": len(fetched),
        "record_count": len(records),
        "observations_path": str(output / "market_intelligence_pit_observations_v1.jsonl"),
        "observations_sha256": sha256(raw),
        "future_join_count": 0,
        "anti_leakage_status": "PASS",
        **SAFETY_FLAGS,
    }
    if write:
        write_once(output / "market_intelligence_pit_observations_v1.jsonl", raw)
        write_once(
            output / "market_intelligence_pit_source_manifest_v1.json", encode(report) + b"\n"
        )
    return {**report, "write_performed": write}
