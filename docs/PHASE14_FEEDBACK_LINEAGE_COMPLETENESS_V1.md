# Phase14 Feedback Lineage Completeness V1

## Objetivo

Corrigir a perda sistêmica de lineage observada no pipeline paper:

```text
Freqtrade paper SQLite
→ Phase14
→ data/trades/inbox/freqtrade_paper_closed_trades.csv
→ outcome_events.parquet
→ paper_closed_trades_incremental.parquet
→ microbatch
```

A evidência operacional mostrou que 55/55 eventos recém-ingeridos estavam sem
`trade_id` e 55/55 sem `exit_reason`. Como consequência, `roi_hit` e
`stoploss_hit` permaneciam falsos mesmo para trades comprovadamente encerrados
por ROI ou stop-loss.

Esta branch corrige o contrato de dados. Ela não altera estratégia, risco,
Freqtrade, Qlib, modelo ativo, Trader Master, containers, live/canary ou ordens.

## Contrato Phase14

`smartcrypto.data.paper_trade_lifecycle.normalize_closed_trades()` passa a
preservar explicitamente:

```text
id nativo do Freqtrade → trade_id
id nativo do Freqtrade → order_id=freqtrade-paper-{id}
exit_reason do Freqtrade → exit_reason
```

`order_id` permanece idêntico ao contrato anterior. Isso preserva a chave
canônica usada pela deduplicação do auto-learning.

## Contrato do auto-learning

O normalizador já suportava `trade_id` e `exit_reason`. Com a origem completa,
os eventos passam a derivar corretamente:

- `roi_hit`;
- `stoploss_hit`;
- `forced_exit`;
- `liquidation_flag`.

O parquet incremental também passa a carregar `trade_id` e as classificações de
saída, além de `exit_reason`.

## Reconciliação histórica

Foi adicionado:

```text
smartcrypto/learning/paper_autolearning/lineage_reconciliation.py
scripts/reconcile_phase14_feedback_lineage_v1.py
```

A reconciliação é **preview-only por padrão**.

```powershell
python scripts/reconcile_phase14_feedback_lineage_v1.py `
  --project-root . `
  --json
```

Nenhum arquivo é alterado nesse modo.

Uma futura execução com `--write` somente é permitida quando os dois destinos
estiverem sob `data/feedback`. A branch não executa essa operação sobre os dados
runtime reais.

## Idempotência

A reconciliação usa `order_id` como identidade primária e não recria eventos.
Ela preserva:

- `event_id`;
- `order_id`;
- `row_fingerprint`;
- quantidade de linhas;
- valores econômicos já registrados.

Campos enriquecidos:

```text
trade_id
exit_reason
roi_hit
stoploss_hit
forced_exit
liquidation_flag
```

A segunda execução sobre um store já enriquecido deve retornar
`update_count=0`.

## Fail-closed

A reconciliação é bloqueada sem escrita quando detecta:

- `order_id` duplicado na fonte;
- `order_id` duplicado no store existente;
- `trade_id` conflitante;
- `exit_reason` conflitante quando ambos já existem;
- divergência de símbolo ou lado;
- divergência de timestamps de abertura/fechamento;
- divergência material de `net_pnl` ou `profit_ratio`;
- colisão de chave de deduplicação após o enrichment;
- tentativa de escrever fora de `data/feedback`.

Em caso de conflito, os eventos reconciliados retornados são os eventos
originais e `write_performed=false`.

## Segurança

O reconciliador declara e preserva:

```text
paper_only=true
shadow_only=true
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
sends_orders=false
exchange_private_access=false
changes_risk=false
writes_runtime=false
writes_sqlite=false
master_update_requested=false
master_update_performed=false
model_promotion_performed=false
active_model_changed=false
```

## Validação prevista

Teste dedicado:

```text
tests/test_phase14_feedback_lineage_completeness_v1.py
```

Cobertura:

1. `trade_id` e `exit_reason` preservados no Phase14;
2. ROI corretamente classificado;
3. stop-loss corretamente classificado;
4. parquet incremental preserva lineage;
5. reconciliação mantém identidade e número de linhas;
6. segunda execução é idempotente;
7. conflito de identidade bloqueia;
8. conflito econômico bloqueia;
9. `order_id` duplicado bloqueia;
10. preview não escreve;
11. escrita em diretório temporário autorizado fica restrita a `data/feedback`;
12. path fora de `data/feedback` é bloqueado.

Nenhuma reconciliação em dados runtime reais é parte da validação desta branch.
