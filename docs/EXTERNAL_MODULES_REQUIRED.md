# Módulos grandes que NÃO estavam no ZIP enviado

Este pacote limpo foi construído apenas com o conteúdo disponível em `FUTUROS.zip`. As seguintes pastas não estavam no upload e, portanto, não foram higienizadas nem copiadas:

- `Bitradex neta` / `Bitradexneta` — prints OCR.
- `bitradex_kline_scraper` — scraper histórico antigo.
- `bitradex_kline_scraper_v3_direct_backfill` — baseline/modelagem Bitradex V11+ se estiver fora do root.
- `bitradex_realtime_candle_collector_v1` — coletor realtime, inclusive 15s. **Preservar.**
- `data` — datasets, SQLite, relatórios, candles e artefatos. **Preservar fora do Git/ZIP limpo.**
- `freqtrade` grande — logs, DB e runtime. Neste ZIP limpo entrou apenas config/strategy mínima, quando presente no upload.

## Regra

Este ZIP é o núcleo limpo de código/configuração. Para rodar o projeto real, recoloque as pastas grandes no mesmo caminho local original antes de executar os comandos.
