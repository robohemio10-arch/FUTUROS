"""Checksum-pinned USD-M aggTrades; availability is modeled event time +1s.

Counts and mean sizes refer to aggregate events, NOT inferred individual fills.
Archive reception is not proof of historical delivery. Empty/uncovered windows
are unavailable; no synthetic trades, flow imputation, or private API access.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import urllib.request
import zipfile
from bisect import bisect_right
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from smartcrypto.research.aibot_parity.market_intelligence_pit_source_foundation import (
    PUBLIC_ROOT,
    download,
    encode,
    sha256,
    write_once,
)

WINDOWS = (60, 300, 900)
DELAY_MS = 1000
RAW_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)
FEATURE_NAMES = (
    "aggressive_buy_volume",
    "aggressive_sell_volume",
    "signed_volume",
    "taker_imbalance",
    "trade_intensity",
    "mean_trade_size",
    "buy_trade_count",
    "sell_trade_count",
    "signed_trade_count_imbalance",
)
FEATURES = tuple(f"{name}_{seconds // 60}m" for seconds in WINDOWS for name in FEATURE_NAMES)
MAX_ARCHIVE_BYTES = 256_000_000


class EventFlowError(ValueError):
    """Public source integrity or event-time coverage failed closed."""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def obtain_archive(
    cache: Path, symbol: str, day: str, *, allow_download: bool
) -> tuple[Path, str, bool]:
    if symbol not in {"BTCUSDT", "ETHUSDT"} or pd.Timestamp(day).strftime("%Y-%m-%d") != day:
        raise EventFlowError("symbol_or_archive_date_invalid")
    relative = f"futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
    target = cache / relative
    checksum_path = target.with_suffix(".zip.CHECKSUM")
    if any(p.is_symlink() for p in (target, checksum_path, *target.parents)):
        raise EventFlowError("cache_symlink_forbidden")
    existed = target.is_file() and checksum_path.is_file()
    if not existed and not allow_download:
        raise EventFlowError(f"SOURCE_CACHE_MISSING:{relative}")
    checksum = (
        checksum_path.read_bytes()
        if checksum_path.is_file()
        else download(PUBLIC_ROOT + relative + ".CHECKSUM")
    )
    tokens = checksum.decode("ascii").split()
    if len(tokens) != 2 or tokens[1].lstrip("*") != target.name or len(tokens[0]) != 64:
        raise EventFlowError("public_checksum_invalid")
    digest = tokens[0]
    if any(c not in "0123456789abcdef" for c in digest):
        raise EventFlowError("public_checksum_invalid")
    if target.is_file():
        if target.stat().st_size > MAX_ARCHIVE_BYTES or file_hash(target) != digest:
            raise EventFlowError("archive_checksum_mismatch")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            request = urllib.request.Request(
                PUBLIC_ROOT + relative,
                headers={"User-Agent": "smartcrypto-event-flow-research/1.0"},
            )
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                if response.url != PUBLIC_ROOT + relative:
                    raise EventFlowError("public_redirect_forbidden")
                hasher = hashlib.sha256()
                size = 0
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise EventFlowError("archive_size_limit")
                        hasher.update(chunk)
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            if hasher.hexdigest() != digest:
                raise EventFlowError("archive_checksum_mismatch")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if file_hash(target) != digest:
                    raise EventFlowError("concurrent_archive_conflict") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    if allow_download:
        write_once(checksum_path, checksum)
    return target, digest, not existed


def archive_chunks(path: Path) -> Iterator[pd.DataFrame]:
    """Read bounded chunks of the original checksummed CSV without extracting it."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if (
            len(entries) != 1
            or not entries[0].filename.endswith(".csv")
            or entries[0].file_size > 2_000_000_000
        ):
            raise EventFlowError("archive_csv_ambiguous_or_oversized")
        with archive.open(entries[0]) as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            if tuple(text.readline().strip().split(",")) != RAW_COLUMNS:
                raise EventFlowError("real_aggtrade_schema_invalid")
        with archive.open(entries[0]) as binary:
            previous_id = -1
            previous_time = -1
            for chunk in pd.read_csv(
                binary,
                chunksize=100_000,
                dtype={
                    "agg_trade_id": "int64",
                    "price": "float64",
                    "quantity": "float64",
                    "first_trade_id": "int64",
                    "last_trade_id": "int64",
                    "transact_time": "int64",
                    "is_buyer_maker": "string",
                },
            ):
                ids, times = chunk.agg_trade_id.to_numpy(), chunk.transact_time.to_numpy()
                if chunk.empty:
                    continue
                if (
                    ids[0] <= previous_id
                    or times[0] < previous_time
                    or (np.diff(ids) <= 0).any()
                    or (np.diff(times) < 0).any()
                ):
                    raise EventFlowError("aggregate_identity_or_timestamp_order_invalid")
                if (chunk.first_trade_id < 0).any() or (
                    chunk.last_trade_id < chunk.first_trade_id
                ).any():
                    raise EventFlowError("trade_id_range_invalid")
                values = chunk[["price", "quantity"]].to_numpy()
                if not np.isfinite(values).all() or (values <= 0).any():
                    raise EventFlowError("price_or_quantity_invalid")
                makers = chunk.is_buyer_maker.str.lower()
                if not makers.isin(["true", "false"]).all():
                    raise EventFlowError("buyer_maker_not_boolean")
                chunk["is_buyer_maker"] = makers.eq("true")
                previous_id, previous_time = int(ids[-1]), int(times[-1])
                yield chunk


def required_archives(candidates: pd.DataFrame) -> list[tuple[str, str]]:
    pairs = set()
    for row in candidates[["symbol", "open_time_utc"]].itertuples(index=False):
        decision = pd.Timestamp(row.open_time_utc)
        if pd.isna(decision) or decision.tzinfo is None:
            raise EventFlowError("decision_timestamp_invalid")
        decision = decision.tz_convert("UTC")
        for time in (decision, decision - pd.Timedelta(seconds=max(WINDOWS))):
            pairs.add((str(row.symbol), time.strftime("%Y-%m-%d")))
    return sorted(pairs)


def align_event_flow(
    candidates: pd.DataFrame, cache: Path, *, allow_download: bool = False
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Windows are (decision-window, decision-1s]; never shifted to future data."""
    groups: dict[tuple[str, str], list[tuple[int, int, int]]] = {}
    decisions = pd.to_datetime(candidates.open_time_utc, utc=True)
    for position, row in enumerate(candidates[["symbol", "open_time_utc"]].itertuples(index=False)):
        original_time = pd.Timestamp(row.open_time_utc)
        if pd.isna(original_time) or original_time.tzinfo is None:
            raise EventFlowError("decision_timestamp_invalid")
        decision = int(decisions.iloc[position].timestamp() * 1000)
        start, end = decision - max(WINDOWS) * 1000, decision - DELAY_MS
        for time in (
            pd.Timestamp(start, unit="ms", tz="UTC"),
            pd.Timestamp(end, unit="ms", tz="UTC"),
        ):
            groups.setdefault((str(row.symbol), time.strftime("%Y-%m-%d")), []).append(
                (position, start, end)
            )
    sums: npt.NDArray[np.float64] = np.zeros((len(candidates), len(WINDOWS), 4), dtype=float)
    archives: dict[str, Any] = {}
    coverage_days: set[tuple[str, str]] = set()
    last_ids: dict[str, int] = {}
    used_hashes: dict[int, set[str]] = {i: set() for i in range(len(candidates))}
    for (symbol, day), requested in sorted(groups.items()):
        requested = sorted(set(requested))
        path, digest, downloaded = obtain_archive(cache, symbol, day, allow_download=allow_download)
        row_count = 0
        first_id: int | None = None
        last_id: int | None = None
        for chunk in archive_chunks(path):
            times = chunk.transact_time.to_numpy(dtype="int64")
            if (
                pd.Timestamp(times[0], unit="ms", tz="UTC").strftime("%Y-%m-%d") != day
                or pd.Timestamp(times[-1], unit="ms", tz="UTC").strftime("%Y-%m-%d") != day
            ):
                raise EventFlowError("archive_event_date_mismatch")
            row_count += len(chunk)
            first_id = int(chunk.agg_trade_id.iloc[0]) if first_id is None else first_id
            last_id = int(chunk.agg_trade_id.iloc[-1])
            quantities = chunk.quantity.to_numpy(dtype=float)
            sell = chunk.is_buyer_maker.to_numpy(dtype=bool)
            columns = np.column_stack([quantities * ~sell, quantities * sell, ~sell, sell])
            cumulative = np.vstack([np.zeros(4), np.cumsum(columns, axis=0)])
            for position, start, end in requested:
                if times[-1] <= start or times[0] > end:
                    continue
                for w, seconds in enumerate(WINDOWS):
                    lower = int(decisions.iloc[position].timestamp() * 1000) - seconds * 1000
                    a, b = bisect_right(times, lower), bisect_right(times, end)
                    if b > a:
                        sums[position, w] += cumulative[b] - cumulative[a]
                used_hashes[position].add(digest)
        if row_count == 0:
            raise EventFlowError("archive_empty")
        if first_id is None or last_id is None or first_id <= last_ids.get(symbol, -1):
            raise EventFlowError("cross_archive_aggregate_identity_collision")
        last_ids[symbol] = last_id
        coverage_days.add((symbol, day))
        archives[str(path.relative_to(cache))] = {
            "url": PUBLIC_ROOT + path.relative_to(cache).as_posix(),
            "sha256": digest,
            "rows": row_count,
            "first_aggregate_id": first_id,
            "last_aggregate_id": last_id,
            "downloaded": downloaded,
        }
    records: list[dict[str, Any]] = []
    for position, row in enumerate(candidates[["symbol", "open_time_utc"]].itertuples(index=False)):
        available = all(
            (row.symbol, time.strftime("%Y-%m-%d")) in coverage_days
            for time in (
                decisions.iloc[position] - pd.Timedelta(milliseconds=DELAY_MS),
                decisions.iloc[position] - pd.Timedelta(seconds=max(WINDOWS)),
            )
        )
        result: dict[str, Any] = {}
        for w, seconds in enumerate(WINDOWS):
            buy, sell, buy_n, sell_n = sums[position, w]
            volume, count = buy + sell, buy_n + sell_n
            if volume <= 0 or count <= 0:
                available = False
            values = [
                buy,
                sell,
                buy - sell,
                (buy - sell) / volume if volume > 0 else None,
                count / seconds,
                volume / count if count > 0 else None,
                int(round(buy_n)),
                int(round(sell_n)),
                (buy_n - sell_n) / count if count > 0 else None,
            ]
            result.update(
                {
                    f"{name}_{seconds // 60}m": value
                    for name, value in zip(FEATURE_NAMES, values, strict=True)
                }
            )
        result["event_flow_covered"] = available
        result["event_flow_provenance_sha256"] = sha256(encode(sorted(used_hashes[position])))
        records.append(result)
    frame = candidates.copy()
    aligned = pd.DataFrame(records, index=frame.index)
    for name in aligned:
        frame[name] = aligned[name]
    covered = int(frame.event_flow_covered.sum())
    return frame, {
        "source": "binance_usdm_public_daily_aggTrades",
        "real_fields": list(RAW_COLUMNS),
        "archives": archives,
        "archive_count": len(archives),
        "covered_count": covered,
        "candidate_count": len(frame),
        "feature_coverage": covered / len(frame) if len(frame) else 0,
        "future_join_count": 0,
        "availability_margin_seconds": DELAY_MS / 1000,
        "availability_classification": "PIT_MODELLED_CONSERVATIVE",
        "historical_reception_proven": False,
        "counts_semantics": "aggregate_events_not_inferred_individual_fills",
    }
