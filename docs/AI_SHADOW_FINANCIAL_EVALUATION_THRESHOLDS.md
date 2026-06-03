# AI Shadow Financial Evaluation Thresholds

## Objetivo

A avaliacao financeira por threshold mede se as decisoes da IA Shadow possuem
edge financeiro observavel. Ela compara `AI_ACCEPT`, `AI_REJECT`,
`SHADOW_ENTRY`, `SHADOW_SKIP` e grupos `threshold_pass`/`threshold_fail` sem
promover modelo, sem alterar registry e sem tocar no signal producer.

## Fonte

A entrada padrao e o JSONL gerado pelo OutcomeTracker:

```text
data/reports/ai_shadow_model_outcomes.jsonl
```

O avaliador tambem aceita parquet, csv, json e jsonl locais para testes e
auditorias controladas.

## Metricas

O relatorio calcula:

- `total_decisions`;
- `matched_outcomes`;
- `unmatched_outcomes`;
- `win_rate`;
- `loss_rate`;
- `average_win`;
- `average_loss`;
- `average_return`;
- `median_return`;
- `expectancy`;
- `gross_profit`;
- `gross_loss`;
- `net_pnl`;
- `profit_factor`;
- `max_drawdown_approx`;
- `best_threshold`;
- `recommended_threshold`;
- `threshold_policy`;
- `sample_warning`;
- `promotion_allowed=false`;
- `auto_promote=false`.

Quando `gross_loss=0` e `gross_profit>0`, o `profit_factor` e reportado como
`null` com nota `gross_loss_zero`. Isso evita divisao invalida e deixa claro que
o valor e matematicamente infinito, mas nao deve ser usado como permissao de
promocao automatica.

## Thresholds

Thresholds padrao:

```text
0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80
```

O `recommended_threshold` e apenas recomendacao analitica. A politica e:

```text
recommend_only_no_auto_promotion
```

O relatorio nunca altera registry, nunca promove modelo e nunca muda a
producao de sinais.

## Amostra Pequena

Quando a quantidade de outcomes casados e menor que `--min-samples`, o relatorio
retorna `status=insufficient_data`, marca:

- `sample_warning=true`;
- `recommendation_confidence=low`;
- `promotion_allowed=false`;
- `auto_promote=false`.

## Uso

Execucao padrao:

```powershell
python .\scripts\evaluate_ai_shadow_financial_thresholds.py
```

Com caminhos explicitos:

```powershell
python .\scripts\evaluate_ai_shadow_financial_thresholds.py `
  --input data/reports/ai_shadow_model_outcomes.jsonl `
  --report data/reports/ai_shadow_financial_threshold_evaluation_report.json `
  --thresholds 0.50,0.55,0.60,0.65,0.70,0.75,0.80 `
  --min-samples 30 `
  --strict
```

## Saida Runtime

Relatorio padrao:

```text
data/reports/ai_shadow_financial_threshold_evaluation_report.json
```

Esse arquivo e runtime e nao deve ser versionado. Arquivos em `data/`,
`models/`, `reports/`, parquet, sqlite, csv, xlsx, logs e evidence permanecem
fora do git.

## Bloqueios

O avaliador bloqueia quando:

- input esta ausente;
- colunas minimas estao ausentes;
- nao ha outcomes casados;
- safety flags sao inseguras;
- `live_trading_enabled=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`;
- `sends_orders=true`;
- `changes_risk=true`.

## Garantias De Seguranca

Este fluxo e paper/shadow only:

- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao altera signal producer;
- nao altera runtime Qlib;
- nao altera registry automaticamente;
- nao promove modelo automaticamente;
- nao altera modelos;
- nao altera risco;
- nao altera Docker;
- nao altera `.env`.
