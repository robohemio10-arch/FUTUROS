# AI Shadow Entry Observer

O AI Shadow Entry Observer e uma camada offline/shadow para observar possiveis entradas da IA, registrar predicoes e criar evidencia para avaliacao futura. Ele nao envia ordens, nao acessa exchange privada, nao altera o dashboard e nao muda o fluxo paper 24h.

## Shadow, paper, testnet e live

- `shadow`: observa sinais e registra decisoes sem executar nada.
- `paper`: pode simular execucao em ambiente controlado, ainda sem ordem real.
- `testnet`: usa ambiente de teste da exchange quando explicitamente configurado.
- `live`: opera conta real. Este observador nao suporta live.

Esta branch implementa somente `shadow`. As flags permanecem travadas:

- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`

## Objetivo

O observador le um dataset limpo de decisao em abertura, treina um modelo simples em memoria para diagnostico shadow e registra uma decisao por linha observada. Quando houver `model_vs_baseline_financial_evaluation_report.json`, o observador usa o melhor modelo suportado informado pelo relatorio para alinhar metadados auditaveis:

- `logistic_regression` vira `logistic_regression_shadow_observer`.
- `random_forest` vira `random_forest_shadow_observer`.
- `model_source` fica no formato `model_vs_baseline_financial_evaluation:<modelo>`.

- `SHADOW_ENTRY`: probabilidade maior ou igual ao threshold.
- `SHADOW_SKIP`: probabilidade abaixo do threshold.
- `BLOCKED`: usado quando a observacao nao pode rodar com seguranca.

O modelo inicial e deliberadamente simples e nao e persistido. Ele existe para exercitar a infraestrutura de observacao, logging e futura avaliacao, nao para liberar operacao.

## Features permitidas

Somente features conhecidas no momento de abertura:

- `open_1m_*`
- `open_5m_*`
- `duration_seconds`, quando ja estiver no dataset limpo e for aceito pela auditoria

Timestamps, ids e `symbol` sao metadados, nao features numericas do modelo.

## Features proibidas

Nunca entram na decisao:

- `target_win`
- `return_pct`
- `net_return_pct`
- `gross_return_pct`
- `leveraged_return_pct`
- `pnl`
- `pnl_resolved`
- `raw_return`
- `raw_return_resolved`
- `exit_price`
- `exit_price_repaired`
- `close_*`
- `mfe_pct`
- `mae_pct`
- `path_candles`
- qualquer coluna `future_ret_*` ou `target_*`

O observador tambem roda auditoria anti-leakage em modo `open` antes de gerar decisoes.

## Saidas

O script gera dois arquivos runtime ignorados pelo Git:

- JSON report: resumo de status, contagens, features usadas/excluidas e amostras.
- JSONL decisions: uma linha por decisao shadow.

Cada decisao contem campos estaveis para leitura futura pelo dashboard:

- `decision_id`
- `created_at`
- `trade_id`
- `symbol`
- `open_1m_ts`
- `model_name`
- `model_version`
- `model_source`
- `probability_win`
- `probability_threshold`
- `decision`
- `decision_reason`
- `feature_count`
- `feature_columns_used`
- flags de seguranca sempre falsas para live/order/private access

## Comando recomendado

```powershell
python scripts/run_ai_shadow_entry_observer.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --model-report data/reports/model_vs_baseline_financial_evaluation_report.json `
  --output data/reports/ai_shadow_entry_observer_report.json `
  --decisions-output data/reports/ai_shadow_entry_decisions.jsonl `
  --id-column trade_id `
  --symbol-column symbol `
  --time-column open_1m_ts `
  --target-column target_win `
  --probability-threshold 0.60 `
  --max-rows 500 `
  --dry-run true `
  --shadow-only true `
  --seed 42
```

## Interpretacao

- `OK`: o observador gerou decisoes shadow sem bloqueio de seguranca ou leakage.
- `WARNING`: rodou, mas ha limitacoes, como ausencia de model report validado.
- `BLOCKED`: flags inseguras, leakage, falta de features abertas ou treino historico insuficiente.

`OK` nao libera live trading. Ele apenas indica que o observador conseguiu registrar sinais shadow.

## Criterios antes de paper operacional

Antes de qualquer uso operacional paper, exigir no minimo:

- 7 dias corridos ou 200 sinais shadow.
- Sem erro runtime.
- Sem leakage.
- Modelo melhor que baseline em avaliacao finance-grade.
- Drawdown simulado aceitavel.
- Revisao por governanca, RiskManager, ledger, preflight, kill switch e FinancialEventLog.

Mesmo apos esses criterios, live trading continua fora de escopo.
