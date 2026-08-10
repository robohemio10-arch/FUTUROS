# PAPER MOMENTUM FORWARD OOS OBSERVER V1

## Objetivo

Observar prospectivamente, em research-only/read-only, o filtro de momentum já congelado:

- `entry_return_12 >= 0.004890587971048965`;
- `entry_return_1 >= 0.0013730468839541765`;
- operador `AND`.

O controle é o conjunto completo de trades paper financeiramente elegíveis fechados após o freeze. Esta branch não bloqueia entradas, não pesquisa novos thresholds e não usa profit-protection.

## Freeze verificável

- freeze commit: `ed4efef093120786bd2b417ecb8d068373879679`;
- início forward UTC: `2026-08-10T00:51:10Z`;
- corte estrito: `close_time_utc > 2026-08-10T00:51:10Z`.

Trades com `close_time_utc` igual ao timestamp de freeze não entram na amostra forward. Trades abertos antes do freeze mas fechados depois permanecem incluídos porque a autorização nominal definiu o corte exclusivamente pelo fechamento; o relatório contabiliza esses casos separadamente.

## Fonte de dados

O runner reutiliza `profit_research_dataset` em `5m`, com:

- `write_report=False`;
- `write_dataset=False`;
- runtime read somente mediante `--allow-runtime-read`;
- nenhuma escrita em banco, parquet, master ou runtime.

A elegibilidade financeira continua herdada de `prepare_profit_dataset`, mantendo excluídas analiticamente amostras conhecidas como financeiramente corrompidas sem alterar o dado original.

## Filtro congelado

A branch importa os thresholds do contrato anterior já validado. Nenhum quantile/grid/random search é executado.

Missing ou valores não finitos em `entry_return_12`/`entry_return_1` falham fechados para o candidato e entram no diagnóstico de cobertura.

## Acumulação de evidência

O observer pode ser executado repetidamente. Cada execução reconstrói a visão corrente do Paper em memória e avalia apenas trades posteriores ao freeze.

Enquanto houver menos de 30 trades candidatos, o resultado permanece:

- `forward_evidence_ready=false`;
- `forward_gate_passed=false`;
- reason `forward_oos_collecting_*`.

A branch não mantém estado persistente e não grava checkpoints.

## Gate financeiro prospectivo

A avaliação somente fica madura quando:

- candidate trades >= 30;
- cobertura de `entry_return_12` + `entry_return_1` = 100% dos trades do controle forward.

Depois disso, o filtro precisa simultaneamente:

- `net_pnl > 0`;
- `expectancy > 0`;
- `profit_factor > 1` quando definido;
- `delta_pnl` contra controle > 0;
- `maximum_drawdown` menor que o controle;
- delta contra controle > 0 na primeira metade temporal;
- delta contra controle > 0 na segunda metade temporal.

O gate não altera execução. Mesmo quando passa:

- `eligible_for_future_paper_wiring_review=true`;
- `ready_for_paper_wiring=false`.

Uma etapa operacional posterior exigiria autorização nominal separada.

## Diagnósticos

O relatório agrega `symbol`, `side` e, quando disponível, `regime` somente para diagnóstico. Esses agrupamentos:

- não mudam thresholds;
- não criam filtros adicionais;
- não participam do gate;
- não têm autoridade operacional.

## Métricas

Controle, candidato e segmentos temporais reportam:

- trade count;
- net PnL;
- expectancy;
- profit factor;
- win rate;
- average win;
- average loss;
- gross profit/loss;
- maximum drawdown;
- selection ratio;
- positive PnL retention;
- delta vs control.

Também são reportados:

- feature coverage;
- observation min/max close time;
- duração desde o freeze;
- quantidade de trades abertos antes/igual ao freeze e fechados depois.

## Segurança

Invariantes explícitas:

- `research_only=true`;
- `read_only=true`;
- `paper_only=true`;
- `operational_authority=false`;
- `blocks_entries=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `searches_new_thresholds=false`;
- `uses_profit_protection=false`;
- sem RiskManager/ROI/stoploss;
- sem modelos ativos;
- sem containers;
- sem PR/merge/deploy.

## CLI

```powershell
python scripts/run_paper_momentum_forward_oos_observer_v1.py `
    --project-root E:\FUTUROS `
    --allow-runtime-read `
    --json
```

Sem `--allow-runtime-read`, o runner continua snapshot-first/fail-closed.

## Interpretação

- `forward_oos_collecting_no_post_freeze_trades`: ainda não há trades fechados após o freeze.
- `forward_oos_collecting_insufficient_candidate_evidence`: há evidência forward, porém menos de 30 trades candidatos ou cobertura incompleta.
- `forward_oos_gate_failed`: amostra mínima foi atingida, mas o edge não passou todos os gates financeiros/temporais.
- `forward_oos_gate_passed`: evidência prospectiva suficiente para futura revisão de wiring, sem autorização operacional automática.
