# Sklearn Model Version Compatibility Guard

Este guard transforma warnings recorrentes de compatibilidade `scikit-learn` em um gate institucional auditavel para modelos IA Shadow, challenger/champion metadata, trainer reports e artefatos locais.

## Problema

Modelos salvos via `pickle`/`joblib` podem depender da versao exata do scikit-learn usada no treino/exportacao. Quando o runtime usa outra versao, o scikit-learn pode emitir warnings como:

```text
InconsistentVersionWarning: Trying to unpickle estimator from version 1.8.0 when using version 1.7.0
```

Em paper/shadow, esse risco nao deve ficar escondido no console. Ele precisa ser registrado em JSON e bloquear uso agressivo, promocao ou interpretacao operacional quando a politica exigir.

## O Que O Guard Faz

O modulo `smartcrypto/ml/sklearn_compatibility_guard.py`:

- le `sklearn.__version__` do runtime;
- le metadata declarada em sidecar, registry, trainer report, JSONL, YAML, CSV ou Parquet quando aplicavel;
- calcula SHA256 do modelo e da metadata quando os arquivos existem;
- detecta warnings sklearn em logs fornecidos;
- compara versao declarada do modelo com versao runtime;
- gera relatorio runtime em `data/reports/sklearn_model_compatibility_guard_report.json`;
- preserva `promotion_allowed=false` e `auto_promote=false`;
- nunca carrega modelo por padrao;
- nunca altera modelo, registry, dataset, Freqtrade DB, signal producer, Docker ou `.env`.

## Politica

Status possiveis:

- `ok`: versoes compativeis e sem bloqueios;
- `warning`: patch mismatch, metadata parcial em modo nao estrito ou warning sklearn detectado em logs;
- `blocked`: incompatibilidade major/minor, versao futura do modelo, runtime ausente, safety flag insegura, auto promotion ou promotion allowed indevido;
- `missing_metadata`: nenhuma fonte de metadata suficiente;
- `missing_model`: modelo informado nao existe.

Em `--strict`, o guard bloqueia metadata de versao ausente, registry sem identidade/versao para champion/challenger, trainer report sem versao, fonte de metadata ausente e demais violações de politica.

Bloqueios permanentes:

- `live_trading_enabled=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`;
- `sends_orders=true`;
- `changes_risk=true`;
- `auto_promote=true`;
- `promotion_allowed=true` sem gate explicito aprovado.

## CLI

```powershell
python scripts/run_sklearn_model_compatibility_guard.py `
  --model data/models/shadow/<modelo>.joblib `
  --metadata data/models/shadow/<modelo>.metadata.json `
  --registry data/models/registry/model_registry.json `
  --trainer-report data/reports/ai_shadow_incremental_trainer_report.json `
  --logs data/logs/runtime.log `
  --report data/reports/sklearn_model_compatibility_guard_report.json
```

Modo estrito:

```powershell
python scripts/run_sklearn_model_compatibility_guard.py --strict
```

## Campos Do Relatorio

O relatorio contem:

- `runtime_sklearn_version`;
- `runtime_python_version`;
- `model_declared_sklearn_version`;
- `registry_declared_sklearn_version`;
- `trainer_declared_sklearn_version`;
- `compatibility_policy`;
- `compatibility_findings`;
- `blocking_findings`;
- `warnings`;
- `model_hash`;
- `metadata_hash`;
- `promotion_allowed=false`;
- `auto_promote=false`;
- safety flags paper/shadow only.

## Garantias

- Paper/shadow only.
- Nao habilita live.
- Nao envia ordens.
- Nao acessa exchange privada.
- Nao altera Freqtrade DB.
- Nao altera `trades_master`.
- Nao altera `training_dataset.parquet`.
- Nao altera runtime Qlib.
- Nao altera registry automaticamente.
- Nao promove modelo.
- Nao altera modelos.
- Nao retreina modelo.
- Nao altera Docker ou `.env`.
- O relatorio em `data/reports/` e runtime e nao deve ser versionado.
