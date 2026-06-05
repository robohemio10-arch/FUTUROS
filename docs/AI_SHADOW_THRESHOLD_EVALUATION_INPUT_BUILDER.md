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
3. `symbol + side + janela temporal`;
4. `embedded_decision_outcome`, quando a própria decisão SQLite contém PnL financeiro real.

O parâmetro `--max-time-delta-minutes` limita o matching temporal. Linhas sem outcome compatível são mantidas com `matched=false`, `match_method=unmatched` e contadas no relatório.

Outcome posterior é permitido apenas para avaliação financeira. Este artefato não deve ser usado como input de inferência ou decisão online.

## Outcome Embutido

Algumas tabelas institucionais de decisão IA Shadow já carregam outcomes paper/shadow consolidados. Quando não houver match externo por `order_id`, `trade_id` ou tempo, o builder pode usar essas colunas como outcome embutido:

1. `shadow_filtered_pnl_usdt`;
2. `base_policy_pnl_usdt`;
3. `raw_pnl_usdt`;
4. `pnl_usdt`;
5. `pnl_net`;
6. `pnl_fechado`.

Somente valores numéricos finitos são aceitos. Quando usado, o output recebe `matched=true`, `match_method=embedded_decision_outcome`, `match_confidence=1.0`, `pnl_usdt`, `pnl_fechado` e `target_profitable` derivado de `pnl > 0`.

Esses campos não são features de decisão. Eles existem apenas no artefato de avaliação de thresholds financeiros e não devem alimentar inferência, sinais, risco real ou promoção de modelo.

O relatório expõe `embedded_outcome_column_used`, `embedded_outcome_rows`, `external_matched_rows`, `embedded_matched_rows` e `unmatched_reason_counts`.

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
