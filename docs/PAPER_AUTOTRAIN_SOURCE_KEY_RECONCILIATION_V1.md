# Paper Autotrain Source Key Reconciliation V1

## Objetivo

Adicionar um reconciliador research-only/read-only para comparar identidades de trades paper entre fontes heterogêneas.

O problema técnico atual é que o snapshot paper DB expõe identidades nativas no formato `trade_close:*`, enquanto CSV e feedback expõem identidades no formato `order_close:*`.

## Escopo

Esta branch cria uma camada de reconciliação entre:

- paper DB runtime/snapshot;
- CSV de closed trades paper;
- eventos de feedback paper;
- watermark incremental.

O reconciliador classifica grupos por chave normalizada:

- `reconciled`;
- `missing_in_csv`;
- `missing_in_feedback`;
- `missing_in_db`;
- `ambiguous`;
- `conflicting`.

## Estratégia de chave

A chave primária de reconciliação usa:

- `close_time_utc`;
- identificador numérico extraído de `trade_id` ou `order_id`, quando disponível.

Fallbacks conservadores usam:

- `close_time_utc + symbol + side + pnl`;
- `close_time_utc + symbol + side`;
- `close_time_utc` isolado apenas como último recurso.

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

## CLI

Dry-run padrão:

```powershell
python .\scripts\build_paper_autotrain_source_key_reconciliation_v1.py --project-root . --allow-paper-db-read --json
```

Escrita opcional apenas de relatório:

```powershell
python .\scripts\build_paper_autotrain_source_key_reconciliation_v1.py --project-root . --allow-paper-db-read --write-report --json
```

A escrita opcional é limitada a `data/reports`.

## Safety

Flags críticas permanecem bloqueadas:

- `ready_for_microbatch_sync=false`
- `ready_for_sync_execution=false`
- `would_create_microbatch=false`
- `would_write_microbatch=false`
- `would_run_training=false`
- `would_promote_model=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `writes_active_registry=false`
- `writes_signal_file=false`
- `sends_orders=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`

## Autoridade operacional

Este artefato é informacional. Ele não autoriza sync, treino, promoção, runtime, registry ativo, Freqtrade, RiskManager, sinais ou ordens.
