# AI Shadow Quality Veto Trainer V1

## Objetivo

Esta branch cria um trainer research-only para um challenger de qualidade IA Shadow.

O trainer estima `probability_quality`, gera decisões hipotéticas `AI_ACCEPT` / `AI_REJECT` e materializa thresholds por `symbol`, `side` e `regime`.

## Fonte

O trainer consome os contratos já existentes:

- FeatureContract;
- DatasetManifest;
- TargetStore;
- WalkForwardSplit.

Ele usa o mesmo dataset institucional validado pelas etapas de feature contract, target store e split walk-forward anti-leakage.

## Comandos

Dry-run sem treino:

```powershell
python .\scripts\train_ai_shadow_quality_veto_challenger_v1.py --project-root . --json
```

Escrever relatório sem treino:

```powershell
python .\scripts\train_ai_shadow_quality_veto_challenger_v1.py --project-root . --write-report --json
```

Treino research-only explícito:

```powershell
python .\scripts\train_ai_shadow_quality_veto_challenger_v1.py --project-root . --train --write-report --json
```

## Saídas

Com `--write-report`, os artefatos são gravados apenas em `data/reports`:

- `data/reports/ai_shadow_quality_veto_trainer_v1.json`
- `data/reports/ai_shadow_quality_veto_trainer_v1.md`
- `data/reports/ai_shadow_quality_veto_metrics_v1.json`
- `data/reports/ai_shadow_quality_veto_metrics_v1.md`

Com `--train --write-challenger-artifact`, o trainer pode gravar artefato
challenger research-only em:

- `data/models/challengers/ai_shadow_quality_veto_v1/<timestamp>/metadata.json`
- `data/models/challengers/ai_shadow_quality_veto_v1/<timestamp>/model.joblib`
- `data/models/challengers/ai_shadow_quality_veto_v1/<timestamp>/metrics.json`
- `data/models/challengers/ai_shadow_quality_veto_v1/<timestamp>/thresholds.json`

Esses arquivos são runtime/reports e não devem ser versionados.

## Métricas

O relatório inclui:

- `probability_quality`;
- decisão challenger `AI_ACCEPT` / `AI_REJECT`;
- métricas por split;
- thresholds por símbolo/lado/regime;
- agregados de acurácia, F1 e expected value;
- validação de lineage e split count.

## Segurança

O trainer não tem autoridade operacional.

Garantias:

- `promotion_eligible=false`;
- `ai_shadow_runtime_updated=false`;
- `veto_runtime_active=false`;
- `veto_registry_write_performed=false`;
- `registry_write_performed=false`;
- `model_promotion_performed=false`;
- `active_model_changed=false`;
- `qlib_runtime_updated=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `changes_risk=false`;
- `writes_runtime=false`;
- `writes_sqlite=false`.

## Fora de escopo

Esta branch não:

- ativa veto runtime;
- escreve registry ativo;
- promove modelo;
- altera Freqtrade;
- altera RiskManager;
- altera signal producer;
- altera Qlib runtime;
- acessa exchange privada;
- envia ordens.
