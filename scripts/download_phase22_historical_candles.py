from __future__ import annotations
import argparse, json, time
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import pandas as pd

KLINE_COLUMNS = ["open_time","open","high","low","close","volume","close_time","quote_volume","number_of_trades","taker_buy_base_volume","taker_buy_quote_volume","ignore"]

def parse_date(value: str, *, end: bool) -> datetime:
    value = str(value).strip()
    if value.lower() == "today":
        now = datetime.now(timezone.utc)
        return now if end else datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)
    parsed: date | None = None
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]:
        try:
            parsed = datetime.strptime(value, fmt).date()
            break
        except ValueError:
            pass
    if parsed is None:
        raise ValueError(f"Data inválida: {value}. Use YYYY-MM-DD ou DD/MM/YYYY.")
    return datetime.combine(parsed, dtime.max if end else dtime.min, tzinfo=timezone.utc)

def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def fetch_klines(*, base_url: str, endpoint: str, symbol: str, interval: str, start_ms: int, end_ms: int, limit: int, max_retries: int, retry_sleep_seconds: float) -> list[list[Any]]:
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms, "limit": limit}
    url = f"{base_url.rstrip('/')}{endpoint}?{urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "smartcrypto-phase22/1.0"})
            with urlopen(request, timeout=30) as response:
                payload = response.read().decode("utf-8")
            data = json.loads(payload)
            if isinstance(data, dict) and "code" in data:
                raise RuntimeError(f"Binance API error: {data}")
            if not isinstance(data, list):
                raise RuntimeError(f"Resposta inesperada Binance: {data}")
            return data
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                time.sleep(retry_sleep_seconds * attempt)
            else:
                raise RuntimeError(f"Falha ao baixar {symbol} {interval}: {last_error}") from exc
    return []

def normalize_frame(rows: list[list[Any]], symbol: str, interval: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=KLINE_COLUMNS)
    frame = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    frame["symbol"] = symbol
    frame["pair"] = symbol.replace("USDT", "/USDT:USDT")
    frame["tf"] = interval
    for column in ["open","high","low","close","volume","quote_volume","taker_buy_base_volume","taker_buy_quote_volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ["open_time","close_time","number_of_trades"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    frame["ts_ms"] = frame["open_time"].astype("int64")
    frame["ts"] = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True)
    return frame.drop(columns=["ignore"], errors="ignore")

def save_frame(frame: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
        return str(path)
    except Exception:
        csv_path = path.with_suffix(".csv")
        frame.to_csv(csv_path, index=False)
        return str(csv_path)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--start-date", default="2026-01-06")
    parser.add_argument("--end-date", default="today")
    parser.add_argument("--base-url", default="https://fapi.binance.com")
    parser.add_argument("--endpoint", default="/fapi/v1/klines")
    parser.add_argument("--output-dir", default="data/raw/binance_futures_klines")
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--request-sleep-seconds", type=float, default=0.15)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--retry-sleep-seconds", type=float, default=3.0)
    args = parser.parse_args()

    start_dt = parse_date(args.start_date, end=False)
    end_dt = parse_date(args.end_date, end=True)
    if end_dt <= start_dt:
        raise ValueError("end-date precisa ser maior que start-date")
    if args.interval != "1m":
        raise ValueError("A Fase 22 foi desenhada para interval=1m.")
    start_ms = to_ms(start_dt)
    end_ms = to_ms(end_dt)
    interval_ms = 60_000

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "status": "ok",
        "phase": "phase22_historical_market_backfill_download",
        "base_url": args.base_url,
        "endpoint": args.endpoint,
        "symbols": args.symbols,
        "interval": args.interval,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "files": [],
        "total_rows": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    for symbol in args.symbols:
        cursor = start_ms
        chunks: list[pd.DataFrame] = []
        requests = 0
        while cursor <= end_ms:
            rows = fetch_klines(base_url=args.base_url, endpoint=args.endpoint, symbol=symbol, interval=args.interval, start_ms=cursor, end_ms=end_ms, limit=min(args.limit, 1500), max_retries=args.max_retries, retry_sleep_seconds=args.retry_sleep_seconds)
            requests += 1
            if not rows:
                break
            frame = normalize_frame(rows, symbol, args.interval)
            frame = frame[(frame["ts_ms"] >= cursor) & (frame["ts_ms"] <= end_ms)]
            if frame.empty:
                break
            chunks.append(frame)
            last_open = int(frame["ts_ms"].max())
            print(json.dumps({"symbol": symbol, "chunk_rows": len(frame), "last_ts": pd.to_datetime(last_open, unit="ms", utc=True).isoformat(), "requests": requests}, ensure_ascii=False))
            next_cursor = last_open + interval_ms
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            time.sleep(args.request_sleep_seconds)
            if len(rows) < min(args.limit, 1500):
                break

        combined = pd.concat(chunks, ignore_index=True) if chunks else normalize_frame([], symbol, args.interval)
        if not combined.empty:
            combined = combined.drop_duplicates(subset=["symbol", "tf", "ts_ms"]).sort_values(["symbol", "tf", "ts_ms"])
        suffix_start = start_dt.strftime("%Y%m%d")
        suffix_end = end_dt.strftime("%Y%m%d")
        saved_path = save_frame(combined, output_dir / f"{symbol}_{args.interval}_{suffix_start}_{suffix_end}.parquet")
        file_report = {"symbol": symbol, "interval": args.interval, "rows": int(len(combined)), "requests": requests, "path": saved_path, "min_ts": combined["ts"].min().isoformat() if not combined.empty else None, "max_ts": combined["ts"].max().isoformat() if not combined.empty else None}
        report["files"].append(file_report)
        report["total_rows"] += int(len(combined))

    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/phase22_download_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
