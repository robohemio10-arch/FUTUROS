# Bitradex Realtime Candle Collector — V3 Hotfix

## Objetivo

Coletar dados públicos de mercado da BitradeX para BTCUSDT e ETHUSDT em futuros, sem login, sem chave, sem endpoint privado e sem envio de ordem.

A V3 adiciona fallback institucional: quando a BitradeX não expõe candles nativos por REST/TradingView para sessão pública, o coletor agrega candles ao vivo a partir de mensagens públicas de ticker/WebSocket.

## Modos

- `probe`: testa endpoints públicos candidatos de kline.
- `capture`: abre navegador por tempo limitado e captura tráfego de rede.
- `daemon`: captura contínua.
- `stats`: mostra estatísticas do SQLite.
- `export`: exporta CSV/Parquet a partir do SQLite.

## Observação crítica

Se `probe` retornar `hit_count = 0`, isso significa que os endpoints diretos testados não entregaram candles nativos. A V3 ainda pode gerar candles em tempo real por agregação de ticker público. Esses registros ficam marcados em `transport` com `ticker_aggregated`.

## Aplicar

Extraia este hotfix sobre:

```powershell
E:\FUTUROS\bitradex_realtime_candle_collector_v1
```

Depois:

```powershell
cd "E:\FUTUROS\bitradex_realtime_candle_collector_v1"
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="src"
pip install -r requirements.txt
```

## Teste recomendado

```powershell
python -m bitradex_realtime_collector.main `
  --mode capture `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --capture-seconds 300 `
  --scroll-rounds 10 `
  --export-every-seconds 30 `
  --heartbeat-seconds 30 `
  --disable-route-validation `
  --audit-all-network `
  --verbose
```

Conferir:

```powershell
python -m bitradex_realtime_collector.main --mode stats
Get-ChildItem .\data\output -Force
Get-ChildItem .\data\raw -Force
```

## Rodar contínuo

```powershell
python -m bitradex_realtime_collector.main `
  --mode daemon `
  --symbols BTCUSDT ETHUSDT `
  --timeframes 1m 5m 15m `
  --capture-seconds 0 `
  --scroll-rounds 5 `
  --export-every-seconds 60 `
  --heartbeat-seconds 30 `
  --disable-route-validation
```

## Saídas

```text
data/output/bitradex_live_candles.sqlite
data/output/bitradex_btcusdt_futures_1m.csv
data/output/bitradex_btcusdt_futures_5m.csv
data/output/bitradex_btcusdt_futures_15m.csv
data/output/bitradex_ethusdt_futures_1m.csv
data/output/bitradex_ethusdt_futures_5m.csv
data/output/bitradex_ethusdt_futures_15m.csv
```

## Auditoria

```text
data/raw/captured_ws_frames.jsonl
data/raw/network_audit.jsonl
data/raw/captured_payloads.jsonl
data/runtime/direct_probe_summary.json
logs/bitradex_realtime_candle_collector.log
```

## Segurança

O coletor não usa login, chave, cookies privados, endpoints privados, saldo, posição ou ordens.

