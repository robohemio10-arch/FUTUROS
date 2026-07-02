# Financial Label And Triple Barrier Target Store V1

Esta branch cria uma camada report-only para materializar labels financeiros derivados de trades paper fechados. O objetivo e separar targets de features, preservar o contrato anti-leakage e preparar um schema compativel com triple barrier sem declarar um triple barrier completo.

## Fontes

O builder le, quando disponiveis:

- `data/reports/ai_unified_feature_contract_v1.json`
- `data/reports/ai_unified_dataset_manifest_v1.json`
- `data/feedback/training_microbatches/*.parquet`
- `data/feedback/outcome_events.parquet`
- `data/feedback/paper_closed_trades_incremental.parquet`
- `data/reports/paper_feedback_master_consolidation_preview_v1.json`

O dataset selecionado precisa conter outcomes fechados com `net_pnl`, `profit_ratio` e pelo menos `label_sign` ou `label_win_loss`.

## Targets

O TargetStore gera colunas `target_*` separadas das features:

- sinal e win/loss;
- PnL liquido e profit ratio;
- retorno liquido apos custos conhecidos;
- flags de ROI, stoploss e time exit;
- holding time e buckets;
- componentes de valor esperado, custo e penalidade de risco;
- label triple-barrier derivado de trade fechado.

Essas colunas nao podem entrar em `feature_columns`.

## Triple Barrier

O modo desta branch e:

`triple_barrier_mode=closed_trade_derived_v1`

Isso significa que o label e derivado do outcome fechado. A branch nao reconstrui caminho intrabar candle a candle e nao deve afirmar triple barrier completo:

- `intrabar_price_path_available=false`
- `candle_path_required_for_full_triple_barrier=true`

## Saidas

Por padrao o CLI nao escreve arquivos. Com `--write`, grava apenas JSON/Markdown em `data/reports`:

- `data/reports/financial_label_target_store_v1.json`
- `data/reports/financial_label_target_store_v1.md`
- `data/reports/financial_label_target_store_summary_v1.json`
- `data/reports/financial_label_target_store_summary_v1.md`

Esses arquivos sao artefatos runtime e nao devem ser versionados.

## Uso

```powershell
python .\scripts\build_financial_label_target_store_v1.py --project-root . --json
python .\scripts\build_financial_label_target_store_v1.py --project-root . --write --json
```

## Garantias

Esta camada nao treina modelos, nao altera registry, nao promove modelo, nao escreve SQLite, nao altera datasets oficiais, nao altera Qlib runtime, nao altera IA Shadow runtime, nao envia ordens e nao acessa exchange privada. O escopo e paper/shadow only.
