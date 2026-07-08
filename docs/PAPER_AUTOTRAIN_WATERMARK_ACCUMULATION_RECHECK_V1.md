# Paper Autotrain Watermark Accumulation Recheck V1

## Objetivo

Esta branch cria um recheck research-only para validar o estado correto depois
da correcao do watermark incremental dos microbatches paper em quarentena.

O diagnostico responde se os microbatches acumulados contem registros novos ou
apenas reobservacoes dos mesmos trades ja marcados no watermark. O resultado
esperado no estado atual e `AGUARDAR_NOVOS_TRADES_PAPER`: a esteira esta
bloqueada por ausencia de novos registros, nao por falha de codigo.

## Fontes lidas

O recheck pode ler:

- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`
- `data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json`
- `data/reports/paper_autotrain_incremental_watermark_fix_v1.json`
- `data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json`
- `data/reports/paper_autotrain_evidence_accumulation_window_v1.json`
- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`
- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`

Fontes opcionais ausentes entram como warnings controlados.

## Escritas permitidas

Por padrao o CLI nao escreve nada.

Com `--write-report`, ele escreve somente:

- `data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json`
- `data/reports/paper_autotrain_watermark_accumulation_recheck_v1.md`

Nao escreve parquet, SQLite, runtime, registry ativo, modelo, sinal ativo ou
arquivo de Freqtrade.

## Interpretacao

- `source_row_count` mede linhas brutas observadas nos microbatches.
- `unique_record_count` mede registros unicos usando a hierarquia canonica da
  Branch 67.
- `watermark_seen_record_count` mede registros ja marcados como vistos.
- `new_unique_records_count=0` com `watermark_status=ok` significa que a
  acumulacao nao deve contar as linhas brutas como evidencia nova.
- `watermark_prevents_reaccumulation=true` confirma que a repeticao foi
  bloqueada pela correcao incremental.

## Fronteira operacional

Este componente nao tem autoridade operacional. Ele nao:

- treina modelo;
- promove modelo;
- escreve registry ativo;
- altera runtime;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- cria scheduler;
- acessa exchange privada;
- envia ordens.

## Uso

```powershell
python .\scripts\build_paper_autotrain_watermark_accumulation_recheck_v1.py --project-root . --json
python .\scripts\build_paper_autotrain_watermark_accumulation_recheck_v1.py --project-root . --write-report --json
```

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_paper_autotrain_watermark_accumulation_recheck_v1.py -q
python .\scripts\build_paper_autotrain_watermark_accumulation_recheck_v1.py --project-root . --json
python .\scripts\build_paper_autotrain_watermark_accumulation_recheck_v1.py --project-root . --write-report --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```
