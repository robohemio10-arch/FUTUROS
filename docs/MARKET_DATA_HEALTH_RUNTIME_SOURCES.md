# Market Data Health Runtime Sources

Esta frente adiciona uma camada runtime paper/shadow para coletar fontes publicas usadas pelo `MarketDataHealthGuard`.

Ela coleta somente dados publicos da Binance USDT-M Futures REST quando disponivel, sem chave, sem endpoint privado, sem leitura de saldo, sem envio de ordens e sem inicializar Freqtrade live.

## Artefatos Runtime

Os arquivos gerados sao runtime e nao devem ser versionados:

- `data/runtime/market_health/ticker.json`
- `data/runtime/market_health/order_book.json`
- `data/runtime/market_health/trades.json`
- `data/runtime/market_health/rest_snapshot.json`
- `data/runtime/market_health/ws_heartbeat.json`
- `data/reports/market_data_health_runtime_sources_report.json`

## Coleta

Comando padrao:

```powershell
python scripts/collect_market_data_health_runtime_sources.py `
  --symbols BTCUSDT ETHUSDT `
  --output-dir data/runtime/market_health `
  --report data/reports/market_data_health_runtime_sources_report.json `
  --timeout-seconds 5
```

Modo estrito:

```powershell
python scripts/collect_market_data_health_runtime_sources.py --strict
```

Em testes ou ambientes sem WebSocket real, o heartbeat pode ser simulado como artefato paper/shadow. O arquivo marca explicitamente:

```json
"simulated": true
```

## Métricas

O coletor calcula ou persiste:

- `spread_bps`;
- `top_of_book_depth`;
- `estimated_slippage_bps`;
- `latency_ms`;
- `last_ticker_age_seconds`;
- `last_order_book_age_seconds`;
- `rest_snapshot_age_seconds`;
- `ws_heartbeat_age_seconds`;
- `ws_rest_timestamp_delta_seconds`.

Falhas de rede sao convertidas em `warning` ou `blocked` em modo `--strict`, nunca em crash bruto.

## Integração Com MarketDataHealth

O audit atual ja aceita fontes opcionais:

```powershell
python scripts/run_market_data_health_audit.py `
  --candles data/features/market_features_60d.parquet `
  --ticker data/runtime/market_health/ticker.json `
  --order-book data/runtime/market_health/order_book.json `
  --trades data/runtime/market_health/trades.json `
  --rest-snapshot data/runtime/market_health/rest_snapshot.json `
  --ws-heartbeat data/runtime/market_health/ws_heartbeat.json `
  --report data/reports/market_data_health_audit_report.json
```

O modo antigo apenas com `--candles` permanece compativel, mas pode continuar gerando `missing_data` para SpreadGuard, LiquidityGuard, LatencyGuard e WsRestDivergenceGuard se as fontes runtime nao forem fornecidas.

## Segurança

- Paper/shadow only.
- Nao habilita live.
- Nao habilita order submission.
- Nao acessa exchange privada.
- Nao le saldo.
- Nao envia ordens.
- Nao altera Freqtrade DB.
- Nao altera `trades_master`.
- Nao altera `training_dataset.parquet`.
- Nao altera signal producer.
- Nao altera modelos ou registry.
- Nao altera risco operacional real.
- Nao altera `.env`.
