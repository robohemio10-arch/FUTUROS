# AI Shadow Threshold Evaluation Input Builder

Este builder cria o artefato contratual para `evaluate_ai_shadow_financial_thresholds.py`.

Ele une decisões IA Shadow com outcomes, microbatch incremental ou feedback paper fechado, produzindo um parquet com as colunas obrigatórias:

- `matched`;
- `probability_or_confidence`;
- `decision`.

O builder não inventa probabilidade. Se nenhuma fonte tiver `probability`, `probability_win`, `confidence`, `proba`, `score`, `model_confidence` ou `ai_score`, o status será `blocked` com `missing_probability`.

## Entradas

Fontes opcionais:

- `--decisions`
- `--outcomes`
- `--microbatch`
- `--paper-feedback`
- `--sqlite-decisions`

SQLite é lido em modo read-only quando possível. O builder não altera SQLite de origem.

## Saídas

Runtime, não versionadas:

- `data/reports/ai_shadow_threshold_evaluation_input.parquet`
- `data/reports/ai_shadow_threshold_evaluation_input_report.json`

## Matching

Ordem de preferência:

1. `order_id` exato;
2. `trade_id` exato;
3. `symbol + side + janela temporal`.

O parâmetro `--max-time-delta-minutes` limita o matching temporal. Linhas sem outcome compatível são mantidas com `matched=false`, `match_method=unmatched` e contadas no relatório.

Outcome posterior é permitido apenas para avaliação financeira. Este artefato não deve ser usado como input de inferência ou decisão online.

## Uso

```powershell
python scripts/build_ai_shadow_threshold_evaluation_input.py `
  --decisions data/reports/ai_shadow_model_decisions.jsonl `
  --outcomes data/reports/ai_shadow_model_outcomes.jsonl `
  --microbatch data/features/incremental_training_microbatch.parquet `
  --paper-feedback data/feedback/paper_closed_trades_incremental.parquet `
  --sqlite-decisions data/runtime/ai_shadow_filter_decisions.sqlite `
  --output data/reports/ai_shadow_threshold_evaluation_input.parquet `
  --report data/reports/ai_shadow_threshold_evaluation_input_report.json
```

Depois:

```powershell
python scripts/evaluate_ai_shadow_financial_thresholds.py `
  --input data/reports/ai_shadow_threshold_evaluation_input.parquet `
  --report data/reports/ai_shadow_financial_threshold_evaluation_report.json
```

## Garantias

- Paper/shadow only.
- Não habilita live trading.
- Não habilita order submission.
- Não acessa exchange privada.
- Não envia ordens.
- Não altera Freqtrade DB.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não altera modelos.
- Não promove modelo.
- Não altera registry.
- Não altera signal producer.
- Não altera risco operacional real.
- Não altera `.env`.
