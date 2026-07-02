# Paper Auto-learning Foundation V1

## Objetivo

Esta branch fecha o loop minimo de autoaprendizado paper/shadow:

```text
closed trades paper
-> feedback store incremental
-> outcome_events com schema de futuros perpetuos
-> microbatch diario simples
-> training smoke/advisory para Qlib challenger e IA Shadow challenger
```

O fluxo e fundacional. Ele nao implementa treino institucional final, registry,
scheduler, triple-barrier completo, Qlib nativo completo ou promocao de modelo.

## Fontes permitidas

- `data/trades/inbox/freqtrade_paper_closed_trades.csv`
- `data/feedback/paper_closed_trades_incremental.parquet`
- `data/reports/paper_closed_trades_readonly_source_contract_v1.json`

O runner tambem aceita `--source` para apontar explicitamente uma fonte local
permitida de testes ou operacao.

## Saidas permitidas

Somente com `--write-feedback`:

- `data/feedback/paper_closed_trades_incremental.parquet`
- `data/feedback/outcome_events.parquet`
- `data/feedback/training_microbatches/YYYY-MM-DD.parquet`
- `data/reports/paper_autolearning_foundation_summary.json`
- `data/reports/paper_autolearning_foundation_summary.md`

Esses arquivos sao runtime/data e nao devem ser versionados.

## Deduplicacao

A politica e:

1. `order_id`
2. `internal_order_id`
3. `trade_id`
4. `row_fingerprint`

Duplicatas nao geram novo `outcome_event`.

## Schema de outcome

O schema `outcome_events` inclui campos especificos de futuros perpetuos:

- `market_type=futures_perpetual`
- `margin_mode`
- `leverage`
- `funding_fee`
- `trading_fee`
- `liquidation_price`
- `distance_to_liquidation_pct`
- `pnl_on_margin_pct`
- `pnl_on_notional_pct`

Campos ausentes na fonte permanecem no schema e entram no relatorio de coverage.

## Microbatch

O microbatch inclui apenas trades fechados validos. Ele bloqueia qualquer coluna
`future_ret_*` e nao transforma outcomes ou labels em features preditivas.

O objetivo do microbatch e preparar evidencia diaria simples. Ele nao altera
`training_dataset.parquet`.

## Training smoke

Com `--train-smoke`, o runner executa apenas checks advisory:

- se ha linhas;
- se ha features;
- se ha mais de uma classe de label;
- se um futuro challenger poderia ser treinado.

Ele nao cria modelo ativo, nao altera champion, nao altera registry e nao toca
runtime Qlib ou IA Shadow.

## Comandos

No-write padrao:

```powershell
python .\scripts\run_paper_autolearning_foundation_v1.py --project-root . --no-write --json
```

Escrever feedback permitido:

```powershell
python .\scripts\run_paper_autolearning_foundation_v1.py --project-root . --write-feedback --json
```

Smoke advisory:

```powershell
python .\scripts\run_paper_autolearning_foundation_v1.py --project-root . --write-feedback --train-smoke --json
```

## Garantias de seguranca

Sempre preserva:

- `paper_only=true`
- `shadow_only=true`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `master_update_requested=false`
- `master_update_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`

Fora de escopo:

- atualizar `trades_master`;
- escrever XLSX;
- scheduler;
- registry;
- triple-barrier completo;
- treino Qlib nativo completo;
- promocao de modelo;
- alterar `signal_producer`;
- alterar RiskManager, Qlib runtime ativo ou IA Shadow runtime ativo;
- live/canary/orders;
- exchange privada.
