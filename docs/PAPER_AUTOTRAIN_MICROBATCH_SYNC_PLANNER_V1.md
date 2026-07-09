# Paper Autotrain Microbatch Sync Planner V1

## Objetivo

Adicionar um planner research-only/read-only para sincronização futura de microbatches paper, sem executar a sincronização.

A branch consome as fontes já diagnosticadas pela esteira de autotrain paper:

- paper DB runtime/snapshot via resolver read-only;
- CSV de closed trades paper;
- eventos de feedback paper;
- microbatches existentes em quarentena;
- watermark incremental.

## Escopo

O planner calcula:

- novos registros por fonte;
- registros ausentes no microbatch por fonte;
- divergências nativas entre paper DB, CSV e feedback;
- necessidade de reconciliação antes de qualquer executor;
- plano auditável de sync em modo dry-run.

## Fora de escopo

Esta branch não:

- cria microbatch;
- escreve parquet operacional;
- escreve SQLite;
- atualiza runtime;
- treina modelo;
- promove modelo;
- altera registry ativo;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- cria scheduler;
- envia ordens;
- acessa exchange privada.

## Decisões

### `PLANEJAR_SYNC_MICROBATCHES_PAPER_RESEARCH_ONLY`

Usada quando as fontes estão alinhadas e há registros ausentes do microbatch, mas a execução não é autorizada nesta branch.

### `RECONCILIAR_FONTES_PAPER_ANTES_DE_SYNC`

Usada quando paper DB, CSV e feedback divergem por contagem ou por chaves nativas. Nesse caso, a branch produz plano, mas mantém o estado bloqueado até reconciliação.

### `PROVER_FONTE_AUTORITATIVA_PAPER_DB_READONLY`

Usada quando a leitura explícita do paper DB não foi autorizada ou a fonte está ausente/inválida.

## Safety

Flags críticas permanecem bloqueadas:

- `would_create_microbatch=false`
- `would_write_microbatch=false`
- `would_run_training=false`
- `would_promote_model=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `writes_active_registry=false`
- `sends_orders=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`

## CLI

Dry-run padrão:

```powershell
python .\scripts\build_paper_autotrain_microbatch_sync_planner_v1.py --project-root . --allow-paper-db-read --json
```

Escrita opcional apenas de relatório:

```powershell
python .\scripts\build_paper_autotrain_microbatch_sync_planner_v1.py --project-root . --allow-paper-db-read --write-report --json
```

A escrita opcional é limitada a `data/reports`.

## Estado esperado no ambiente atual

O planner deve permanecer bloqueado com:

- `status=blocked`
- `decision=RECONCILIAR_FONTES_PAPER_ANTES_DE_SYNC`
- `reason=source_reconciliation_required_before_sync_execution`
- `sync_plan_status=blocked_requires_source_reconciliation`

Esse bloqueio é esperado porque as fontes atuais divergem:

- paper DB snapshot: 513 registros novos pós-watermark;
- CSV closed trades: 538 registros novos;
- feedback events: 498 registros novos;
- microbatch: 0 registros novos.

## Autoridade operacional

Este artefato é informacional. Ele não autoriza execução de sync, treino, promoção, runtime, registry ativo, Freqtrade, RiskManager, sinais ou ordens.
