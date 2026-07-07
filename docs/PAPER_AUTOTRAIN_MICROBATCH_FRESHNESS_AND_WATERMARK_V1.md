# Paper Autotrain Microbatch Freshness and Watermark V1

## Objetivo

Esta branch adiciona um diagnostico research-only/read-only para responder se os microbatches diarios de paper autotrain estao trazendo evidencia nova ou apenas reobservando os mesmos registros. O foco e medir freshness, watermarks e novidade incremental antes de qualquer nova tentativa de treino.

O diagnostico nao treina, nao promove, nao altera runtime, nao escreve parquet, nao escreve SQLite, nao toca Freqtrade, RiskManager, Qlib runtime, IA Shadow runtime, registry ativo ou arquivos de sinais.

## Fontes lidas

Leitura principal:

- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`

Leituras opcionais, sempre com warning controlado se ausentes ou invalidas:

- `data/reports/paper_autotrain_evidence_accumulation_window_v1.json`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json`
- `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`
- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`

## Escritas permitidas

Por padrao nao ha escrita.

Com `--write-report`, somente estes arquivos podem ser escritos:

- `data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json`
- `data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.md`

Nenhum dataset, parquet, SQLite, registry ativo ou artefato runtime e escrito.

## Chave de identidade de evidencia

A deduplicacao/freshness segue hierarquia deterministica:

1. `record_hash`, se preenchido;
2. `order_id + close_time_utc`, se ambos existirem;
3. `trade_id + close_time_utc`, se ambos existirem;
4. `symbol + side + open_time + close_time + pnl`;
5. hash estavel da linha normalizada.

O indice do dataframe nunca e usado como identidade de evidencia.

## Interpretacao dos status

- `blocked / missing_quarantine_microbatch_sources`: ainda nao ha microbatches de quarentena.
- `blocked / microbatch_freshness_stalled`: ha runs, mas todos os runs apos o primeiro reobservam os mesmos registros.
- `warning / freshness_progress_detected_but_evidence_still_insufficient`: existe alguma novidade, mas ainda ha staleness parcial.
- `ok / microbatch_freshness_progressing`: os runs mostram avanco incremental sem staleness critico.

Mesmo `status=ok` nao autoriza treino, promocao, runtime ou rechecagem operacional. E apenas evidencia de freshness.

## Comandos

Diagnostico sem escrita:

```powershell
python .\scripts\build_paper_autotrain_microbatch_freshness_and_watermark_v1.py --project-root . --json
```

Gerar relatorio JSON/Markdown em `data/reports`:

```powershell
python .\scripts\build_paper_autotrain_microbatch_freshness_and_watermark_v1.py --project-root . --write-report --json
```

Fail-closed para qualquer run stale:

```powershell
python .\scripts\build_paper_autotrain_microbatch_freshness_and_watermark_v1.py --project-root . --fail-on-stale --json
```

## Garantias de seguranca

O diagnostico sempre retorna:

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
- `writes_operational_parquet=false`

## Proxima acao

Se o resultado real for `microbatch_freshness_stalled`, a proxima branch deve corrigir o gerador incremental/watermark. Repetir acumulacao sem novidade apenas recicla os mesmos registros e nao aumenta evidencia estatistica real.
