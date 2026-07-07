# Paper Autotrain Incremental Watermark Fix V1

## Objetivo

Esta branch adiciona um watermark incremental research-only para impedir que o produtor de microbatch paper em quarentena recicle os mesmos registros como evidencia nova.

O problema diagnosticado na branch anterior foi: cinco runs, 130 linhas brutas, apenas 26 registros unicos e `duplicate_rate=0.8`. A correcao aqui nao treina nem promove nada; ela apenas calcula e aplica um gate incremental antes de materializar novos artefatos de quarentena.

## Fontes lidas

- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`
- `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`
- reports de evidencia/autotrain existentes
- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`
- `data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json`, se existir

Fontes ausentes viram warning ou estado controlado. Nao ha excecao sem tratamento.

## Escritas permitidas

Por padrao nenhuma escrita.

Com `--write-report`, somente:

- `data/reports/paper_autotrain_incremental_watermark_fix_v1.json`
- `data/reports/paper_autotrain_incremental_watermark_fix_v1.md`

Com `--write-watermark-state`, somente:

- `data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json`

Nenhum parquet, SQLite, registry ativo, modelo ativo, sinal ativo ou runtime e escrito.

## Identidade de registro

A identidade segue a mesma hierarquia da Branch 66:

1. `record_hash`, se preenchido;
2. `order_id + close_time_utc`;
3. `trade_id + close_time_utc`;
4. `symbol + side + open_time + close_time + pnl`;
5. hash estavel da linha normalizada.

O indice do dataframe nunca e usado como identidade.

## Comandos

Dry-run sem escrita:

```powershell
python .\scripts\build_paper_autotrain_incremental_watermark_fix_v1.py --project-root . --json
```

Gerar relatorio:

```powershell
python .\scripts\build_paper_autotrain_incremental_watermark_fix_v1.py --project-root . --write-report --json
```

Inicializar watermark research-only:

```powershell
python .\scripts\build_paper_autotrain_incremental_watermark_fix_v1.py --project-root . --write-watermark-state --json
```

## Integracao com o produtor de quarentena

O produtor `paper_autotrain_daily_quarantine_activation` passa a aplicar o gate antes de:

- escrever microbatch de quarentena;
- treinar challengers de quarentena;
- criar artefatos de candidato;
- atualizar registry de quarentena.

Se o watermark indicar zero registros novos, o produtor retorna bloqueio/no-op controlado e nao escreve microbatch duplicado.

## Garantias de seguranca

O diagnostico e o gate preservam:

- `research_only=true`
- `paper_only=true`
- `shadow_only=true`
- `quarantine_only=true`
- `training_allowed=false`
- `promotion_allowed=false`
- `runtime_allowed=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `writes_active_registry=false`
- `writes_signal_file=false`

Mesmo quando `status=ok`, a autorizacao e apenas para um microbatch incremental futuro em quarentena. Nao ha treino real, promocao, runtime, Freqtrade, RiskManager, ordem ou exchange privada nesta branch.
