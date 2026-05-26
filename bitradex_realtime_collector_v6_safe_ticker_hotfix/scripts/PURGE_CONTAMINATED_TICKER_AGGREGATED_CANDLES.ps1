$ErrorActionPreference = "Stop"

$CollectorDir = "C:\Smart Cripto\FUTUROS\bitradex_realtime_candle_collector_v1"
$Db = Join-Path $CollectorDir "data\output\bitradex_live_candles.sqlite"
$BackupDir = Join-Path $CollectorDir "data\backup"
$Ts = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $BackupDir "bitradex_live_candles_before_v6_purge_$Ts.sqlite"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
Copy-Item $Db $Backup -Force
Write-Host "Backup criado: $Backup"

@'
import sqlite3
from pathlib import Path

DB = Path(r"C:\Smart Cripto\FUTUROS\bitradex_realtime_candle_collector_v1\data\output\bitradex_live_candles.sqlite")

with sqlite3.connect(DB) as conn:
    before = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    by_tf_before = conn.execute("""
        SELECT symbol, timeframe, COUNT(*)
        FROM candles
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """).fetchall()

    deleted = conn.execute("""
        DELETE FROM candles
        WHERE transport LIKE '%ticker_aggregated%'
          AND (
              open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
              OR high < open OR high < close OR high < low
              OR low > open OR low > close OR low > high
              OR (symbol = 'BTCUSDT' AND (
                    open < 10000 OR high < 10000 OR low < 10000 OR close < 10000
                    OR open > 300000 OR high > 300000 OR low > 300000 OR close > 300000
              ))
              OR (symbol = 'ETHUSDT' AND (
                    open < 500 OR high < 500 OR low < 500 OR close < 500
                    OR open > 20000 OR high > 20000 OR low > 20000 OR close > 20000
              ))
              OR (timeframe = '15s' AND high / low > 1.02)
              OR (timeframe = '1m'  AND high / low > 1.05)
              OR (timeframe = '5m'  AND high / low > 1.10)
              OR (timeframe = '15m' AND high / low > 1.20)
          )
    """).rowcount

    after = conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
    by_tf_after = conn.execute("""
        SELECT symbol, timeframe, COUNT(*)
        FROM candles
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
    """).fetchall()

    conn.commit()

print({
    "before": before,
    "deleted": deleted,
    "after": after,
    "by_tf_before": by_tf_before,
    "by_tf_after": by_tf_after,
})
'@ | python -
