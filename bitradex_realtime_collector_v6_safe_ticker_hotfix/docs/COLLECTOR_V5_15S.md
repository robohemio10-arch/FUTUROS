# Collector V5 15s

Adiciona suporte ao timeframe `15s` no coletor Bitradex.

Importante: a Bitradex pode não fornecer histórico nativo de 15 segundos. Neste caso, candles `15s` são gerados a partir dos ticks/preços públicos capturados em tempo real pelo fallback `ticker_aggregated`.

Arquivos alterados:

- `src/bitradex_realtime_collector/config.py`
- `src/bitradex_realtime_collector/processor.py`
- `src/bitradex_realtime_collector/endpoint_probe.py`

Scripts adicionados:

- `scripts/RUN_COLLECTOR_CAPTURE_15S_TEST.ps1`
- `scripts/RUN_COLLECTOR_DAEMON_15S.ps1`
- `scripts/RUN_COLLECTOR_EXPORT_15S.ps1`
