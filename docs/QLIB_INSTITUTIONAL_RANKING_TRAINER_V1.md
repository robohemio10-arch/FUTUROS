# Qlib Institutional Ranking Trainer V1

Esta branch cria um trainer institucional de ranking como challenger research/paper-only. Ele consome FeatureContract, DatasetManifest, TargetStore e WalkForwardSplit ja materializados, preservando lineage por hash e sem autoridade operacional.

## Entradas

- `data/reports/ai_unified_feature_contract_v1.json`
- `data/reports/ai_unified_dataset_manifest_v1.json`
- `data/reports/financial_label_target_store_v1.json`
- `data/reports/financial_label_target_store_summary_v1.json`
- `data/reports/walkforward_anti_leakage_split_engine_v1.json`
- `data/reports/walkforward_baseline_summary_v1.json`
- `data/feedback/training_microbatches/*.parquet`

O trainer usa exatamente `feature_columns` do FeatureContract. Targets e outcomes entram apenas como labels/metrica, nunca como feature.

## Modos

Default:

```powershell
python .\scripts\train_qlib_institutional_ranking_challenger_v1.py --project-root . --json
```

Valida lineage, splits e safety flags sem treinar e sem escrever.

Relatorio:

```powershell
python .\scripts\train_qlib_institutional_ranking_challenger_v1.py --project-root . --write-report --json
```

Grava somente JSON/Markdown em `data/reports`.

Treino research:

```powershell
python .\scripts\train_qlib_institutional_ranking_challenger_v1.py --project-root . --train --write-report --json
```

Se Qlib nao estiver disponivel, retorna `status=blocked` com `reason=qlib_backend_unavailable`. O fallback deterministico so e permitido com `--allow-research-fallback` e permanece `promotion_eligible=false`.

## Saidas permitidas

Com `--write-report`:

- `data/reports/qlib_institutional_ranking_trainer_v1.json`
- `data/reports/qlib_institutional_ranking_trainer_v1.md`
- `data/reports/qlib_institutional_ranking_metrics_v1.json`
- `data/reports/qlib_institutional_ranking_metrics_v1.md`

Com `--train --write-challenger-artifact`:

- `data/models/challengers/qlib_institutional_ranking_v1/<timestamp>/metadata.json`
- `data/models/challengers/qlib_institutional_ranking_v1/<timestamp>/model.json`
- `data/models/challengers/qlib_institutional_ranking_v1/<timestamp>/metrics.json`

Esses artefatos sao runtime/data e nao devem ser versionados.

## Metricas

As metricas sao calculadas por split walk-forward:

- RankIC, SpearmanIC e PearsonIC;
- precision@5, precision@10 e recall@10;
- valor esperado por decil;
- spread top/bottom decile;
- top-k expected value, win rate e profit ratio;
- comparacao contra no-trade, random deterministico e always-allow.

## Garantias

Esta branch nao promove modelo, nao altera champion, nao escreve registry ativo, nao altera Qlib runtime ativo, nao altera IA Shadow runtime, nao altera Freqtrade, RiskManager ou signal producer, nao envia ordens, nao acessa exchange privada e nao escreve SQLite operacional. Qualquer resultado bom continua sendo evidencia de challenger em research.
