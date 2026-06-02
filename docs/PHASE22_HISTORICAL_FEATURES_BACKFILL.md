# Phase 22 Historical Features Backfill

## Objetivo

A Fase 22 endurece o backfill historico de candles Binance Futures 1m para
BTCUSDT e ETHUSDT e transforma esses candles em market features consistentes
para o fluxo paper/shadow do SmartCrypto.

Ela existe para ampliar a cobertura historica usada por `trade_enriched` e
`training_dataset` sem tocar no Freqtrade DB operacional, sem acessar exchange
privada e sem enviar ordens.

## Fontes

- `data/raw/binance_futures_klines/*_1m_*.parquet`
- `data/raw/binance_futures_klines/*_1m_*.csv`
- `config/phase22_historical_backfill.yml`

O download usa candles publicos. O build de features e local, read/write apenas
em arquivos do projeto.

## Builder

Script principal:

```powershell
python scripts/build_phase22_market_features.py `
  --raw-dir data/raw/binance_futures_klines `
  --symbols BTCUSDT ETHUSDT `
  --interval 1m `
  --output data/features/market_features_1m_backfill.parquet `
  --main-features data/features/market_features_60d.parquet `
  --sqlite data/sqlite/trading_dataset.sqlite `
  --sqlite-table market_features `
  --update-main-features `
  --backup
```

O builder:

- valida `symbol`, timestamp ou `open_time`, `open`, `high`, `low`, `close` e
  `volume`;
- quando `symbol` nao existe no arquivo bruto, infere o simbolo do filename no
  formato `<SYMBOL>_1m_...`, desde que `SYMBOL` esteja no conjunto permitido;
- preserva timestamps em UTC;
- normaliza explicitamente colunas duplicadas antes de conversoes numericas;
- garante que `pd.to_numeric` receba `Series` 1D;
- gera timeframes `1m` e `5m`;
- remove colunas de lookahead como `future_ret_*`;
- ordena por `symbol`, `tf` e `ts_ms`;
- grava relatorios JSON controlados.

`market_features_60d.parquet` é tratado como arquivo operacional. Ele deve
permanecer sem `future_ret_*` mesmo quando o arquivo existente continha labels
antigas antes do merge. O relatório registra `output_schema_status`,
`operational_feature_schema_ok` e `lookahead_columns_removed`.

## Relatorios

Arquivos gerados:

- `data/reports/phase22_features_report.json`
- `data/reports/phase22_data_quality_report.json`
- `data/reports/phase22_output_summary.json` via inspetor

Campos principais:

- `status`
- `reason`
- `rows`
- `min_ts`
- `max_ts`
- `symbols`
- `timeframes`
- `duplicate_columns`
- `raw_files_total`
- `raw_files_ok`
- `raw_files_skipped`
- `raw_files_blocked`
- `skipped_paths`
- `blocked_paths`

`status=ok` indica que o backfill foi construido e validado. `status=blocked`
indica falha controlada; consulte `reason`.

## Inferencia De Symbol

Alguns raws reais da Fase 22 podem vir sem coluna `symbol`. Nesses casos, o
builder usa uma inferencia explicita e auditavel:

1. o arquivo precisa ter nome no formato `<SYMBOL>_1m_...`;
2. `SYMBOL` precisa estar na lista permitida passada em `--symbols`;
3. a inferencia so ocorre quando a coluna `symbol` esta ausente.

Cada item de `raw_file_reports` registra:

- `symbol_inferred`: `true` ou `false`;
- `inferred_symbol`: simbolo inferido ou `null`;
- `symbol_source`: `column`, `filename` ou `missing`.

Se o simbolo nao puder ser inferido, o arquivo entra como `status=blocked` com
o path exato em `blocked_paths`. Isso nao zera a agregacao quando outros raws
validos existem.

## CSV E Parquet Duplicados

Quando existem `.csv` e `.parquet` com o mesmo stem, o builder prefere
`.parquet` e registra o `.csv` como `status=skipped`.

Alem disso, apos concatenar os arquivos validos, os candles sao deduplicados por
`symbol`, `tf` e `ts_ms`. Essa segunda barreira protege contra arquivos de
periodos sobrepostos.

## Protecao De Overwrite

`market_features_60d.parquet` so deve ser atualizado com `--update-main-features`
quando o build atual terminou com relatorio `ok`.

Se o arquivo principal ja existir, o builder bloqueia a atualizacao sem
`--backup`, retornando:

```text
main_features_backup_required_before_overwrite
```

O backup padrao fica em `data/backups/phase22/<timestamp>/`.

## Sequencia Operacional

1. Baixar candles historicos, quando necessario:

```powershell
python scripts/download_phase22_historical_candles.py
```

1. Construir features historicas:

```powershell
python scripts/build_phase22_market_features.py --update-main-features --backup
```

1. Inspecionar saidas:

```powershell
python scripts/inspect_phase22_outputs.py
python scripts/collect_phase22_summary.py
```

1. Reconstruir datasets pela autoridade oficial da Fase 5:

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1
.\paper_controlado_fase_05\RUN_PHASE5_VERIFY_OUTPUTS.ps1
```

Nao ha importacao automatica de trades nesta etapa. A Fase 5 continua sendo a
via oficial para rebuild de `trades_master`, `trade_enriched` e
`training_dataset`.

## Garantias De Seguranca

- runtime documentado como `paper`;
- `shadow_only=true` nos relatorios;
- `live_trading_enabled=false`;
- `order_submission_enabled=false`;
- `real_order_submission_enabled=false`;
- `exchange_private_access=false`;
- sem chamada Freqtrade API;
- sem leitura de conta privada;
- sem envio de ordem.

## Limitacoes

- O build depende de candles brutos ja baixados.
- O arquivo SQLite escrito por este script e o SQLite analitico do projeto, nao
  o DB operacional do Freqtrade paper.
- Features 5m sao derivadas localmente a partir do 1m e representam candles
  fechados por bucket de 5 minutos.
- Colunas de alvo futuro devem ser criadas em fluxo supervisionado proprio, nao
  no arquivo operacional de market features.
