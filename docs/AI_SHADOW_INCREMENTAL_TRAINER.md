# AI Shadow Incremental Trainer

## Objetivo

O trainer incremental IA Shadow treina um modelo challenger a partir do
microbatch paper fechado em:

```text
data/features/incremental_training_microbatch.parquet
```

Ele nao promove modelo, nao altera registry produtivo e nao interfere no runtime
Qlib, Phase13, Freqtrade ou signal producer.

## Entradas

O microbatch precisa conter:

- `target_profitable`;
- colunas numericas `feature_*`;
- nenhum campo `future_ret_*`.

Com a amostra atual pequena, por exemplo 26 linhas, o treinamento pode retornar
`status=ok` se o dataset tiver duas classes e features validas, mas o relatorio
marca:

- `sample_warning=true`;
- `promotion_status=pending`;
- `auto_promote=false`.

## Saidas

O modelo challenger e salvo em:

```text
data/models/shadow/
```

O relatorio controlado e salvo em:

```text
data/reports/ai_shadow_incremental_trainer_report.json
```

Os artefatos em `data/` e `data/models/` sao runtime e nao devem ser
versionados.

## Modelo

O pipeline institucional usa:

- `SimpleImputer(strategy="median")`;
- `StandardScaler`;
- `LogisticRegression`.

Quando ha amostra suficiente por classe, a avaliacao usa split holdout
deterministico e estratificado. Quando a amostra e pequena demais para holdout
confiavel, o relatorio deixa isso explicito via modo diagnostico.

## Metricas

O relatorio inclui:

- accuracy;
- precision;
- recall;
- f1;
- roc_auc quando aplicavel;
- train_rows;
- test_rows.

Tambem inclui metadata do modelo:

- `model_id`;
- `model_version`;
- `trained_at_utc`;
- `input_path`;
- `input_rows`;
- `feature_columns`;
- `target_column`;
- `class_balance`;
- `metrics`;
- `promotion_status`;
- flags de seguranca.

## Bloqueios

O trainer retorna `status=blocked` quando:

- o input nao existe;
- existe qualquer coluna `future_ret_*`;
- falta `target_profitable`;
- nao ha coluna numerica `feature_*`;
- o target contem apenas uma classe;
- `--strict` encontra target invalido.

## Uso

Execucao padrao:

```powershell
python .\scripts\train_ai_shadow_incremental_model.py
```

Com caminhos explicitos:

```powershell
python .\scripts\train_ai_shadow_incremental_model.py `
  --input data/features/incremental_training_microbatch.parquet `
  --model-dir data/models/shadow `
  --report data/reports/ai_shadow_incremental_trainer_report.json `
  --strict
```

## Garantias De Seguranca

Este fluxo e paper/shadow only:

- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera `.env`;
- nao altera Docker;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao promove modelo automaticamente.
