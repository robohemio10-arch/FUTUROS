# Paper Autotrain New Trades Readiness Gate V1

## Objetivo

Este gate research-only/read-only verifica se existem trades paper fechados,
unicos e ainda ausentes do watermark incremental atual. Ele transforma o estado
da esteira em uma decisao auditavel:

- sem novos registros: `AGUARDAR_NOVOS_TRADES_PAPER`;
- com novos registros: `NOVOS_TRADES_PAPER_DETECTADOS_RECHECK_MANUAL_PERMITIDO`.

Mesmo quando novos registros aparecem, o gate nao cria microbatch e nao executa
treino. Ele apenas indica que uma reavaliacao manual de acumulacao pode ser
executada por outra etapa.

## Fontes lidas

O gate pode ler:

- `data/research/paper_autotrain_daily_quarantine_watermark/watermark_v1.json`
- `data/reports/paper_autotrain_incremental_watermark_fix_v1.json`
- `data/reports/paper_autotrain_watermark_accumulation_recheck_v1.json`
- `data/reports/paper_autotrain_microbatch_freshness_and_watermark_v1.json`
- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`
- `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json`

Fontes opcionais ausentes geram warnings controlados.

## Escritas

Por padrao, nenhuma escrita.

Com `--write-report`, o gate escreve somente:

- `data/reports/paper_autotrain_new_trades_readiness_gate_v1.json`
- `data/reports/paper_autotrain_new_trades_readiness_gate_v1.md`

Nao escreve watermark, parquet, SQLite, registry ativo, modelos, sinais ou
runtime.

## Decisoes

`AGUARDAR_NOVOS_TRADES_PAPER` significa que todas as chaves unicas observadas
nas fontes atuais ja existem no watermark.

`NOVOS_TRADES_PAPER_DETECTADOS_RECHECK_MANUAL_PERMITIDO` significa que ha
chaves novas. O unico efeito permitido e informar que o proximo recheck manual
de acumulacao esta liberado.

## Fronteira operacional

Este gate nao possui autoridade operacional. Ele nao:

- cria microbatch;
- treina modelo;
- avalia candidato;
- promove modelo;
- altera runtime;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- escreve registry ativo;
- escreve sinais;
- registra scheduler;
- acessa exchange privada;
- envia ordens.

## Uso

```powershell
python .\scripts\build_paper_autotrain_new_trades_readiness_gate_v1.py --project-root . --json
python .\scripts\build_paper_autotrain_new_trades_readiness_gate_v1.py --project-root . --write-report --json
```

## Validacao

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest .\tests\test_paper_autotrain_new_trades_readiness_gate_v1.py -q
python .\scripts\build_paper_autotrain_new_trades_readiness_gate_v1.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```
