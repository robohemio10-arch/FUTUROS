# Walk-Forward Anti-Leakage Split Engine V1

Esta branch cria um motor report-only para splits temporais walk-forward com purging e embargo. O objetivo e provar que a base de aprendizado paper/shadow pode ser dividida sem leakage temporal antes de qualquer treino real de Qlib ou IA Shadow.

## Fontes

O builder le evidencias existentes:

- `data/reports/ai_unified_feature_contract_v1.json`
- `data/reports/ai_unified_dataset_manifest_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/financial_label_target_store_summary_v1.json`
- `data/feedback/training_microbatches/*.parquet`
- `data/feedback/outcome_events.parquet`

## Comportamento

O motor:

- ordena o dataset por `open_time_utc`;
- usa `close_time_utc` ou `target_holding_seconds` para intervalos de label;
- cria janelas temporais deterministicas de treino, validacao e teste;
- remove do treino qualquer linha cujo intervalo de label intercepte validacao/teste;
- aplica embargo derivado de `target_barrier_vertical_seconds`;
- audita leakage em features e janelas;
- calcula baselines financeiros simples sem treino.

Nao ha split aleatorio, shuffle, scaler, modelo, registry ou promocao.

## Embargo

Se o TargetStore informar `target_barrier_vertical_seconds=86400`, o embargo minimo usado e `>=86400` segundos. Override explicito via CLI e aceito apenas para auditoria controlada.

## Baselines

Os baselines sao contabeis e deterministas:

- `no_trade`
- `random_deterministic`
- `always_long`
- `always_short`
- `always_allow`
- `always_block`

Eles nao treinam modelo e nao alteram runtime.

## Saidas

Por padrao, o CLI nao escreve. Com `--write`, grava somente JSON/Markdown em `data/reports`:

- `data/reports/walkforward_anti_leakage_split_engine_v1.json`
- `data/reports/walkforward_anti_leakage_split_engine_v1.md`
- `data/reports/walkforward_baseline_summary_v1.json`
- `data/reports/walkforward_baseline_summary_v1.md`

Esses arquivos sao runtime/report artifacts e nao devem ser versionados.

## Uso

```powershell
python .\scripts\build_walkforward_anti_leakage_split_engine_v1.py --project-root . --json
python .\scripts\build_walkforward_anti_leakage_split_engine_v1.py --project-root . --write --json
```

## Garantias de seguranca

Esta branch nao treina Qlib, nao treina IA Shadow, nao cria registry, nao promove modelo, nao altera champion, nao altera Freqtrade, nao altera RiskManager, nao altera signal producer, nao altera scheduler/deployment, nao altera `trades_master`, nao envia ordens e nao acessa exchange privada. O escopo e paper/shadow only.
