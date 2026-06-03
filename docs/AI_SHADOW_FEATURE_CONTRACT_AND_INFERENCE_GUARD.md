# AI Shadow Feature Contract And Inference Guard

## Objetivo

O `FeatureContract` formaliza o schema esperado para as features usadas pela IA
Shadow. Ele registra a lista e a ordem das colunas, dtypes esperados, politica
de nulos, obrigatoriedade de valores finitos, hashes de ordem/schema e flags de
seguranca paper/shadow only.

O `InferenceGuard` valida qualquer input de inferencia contra esse contrato antes
de o modelo shadow/challenger ser usado. O guard nao executa ordens, nao altera
risco, nao promove modelo e nao escreve em artefatos produtivos.

## CLIs

Construir contrato:

```powershell
python .\scripts\build_ai_shadow_feature_contract.py `
  --input data/features/incremental_training_microbatch.parquet `
  --output data/models/shadow/ai_shadow_feature_contract.json `
  --feature-prefix feature_ `
  --model-id shadow_incremental `
  --model-version v1 `
  --strict
```

Validar input de inferencia:

```powershell
python .\scripts\validate_ai_shadow_inference_input.py `
  --input data/features/incremental_training_microbatch.parquet `
  --contract data/models/shadow/ai_shadow_feature_contract.json `
  --report data/reports/ai_shadow_inference_guard_report.json `
  --strict
```

## Bloqueios

O contrato e o guard bloqueiam ou reportam de forma controlada:

- colunas `future_ret_*`;
- colunas `target_*` usadas como features;
- features ausentes;
- features extras em modo estrito;
- ordem incorreta quando `strict_order=true`;
- dtype incompativel;
- NaN acima da politica permitida;
- infinito;
- range fora do contrato quando min/max estao definidos;
- schema hash invalido;
- safety flags inseguras.

As flags obrigatorias sao:

- `paper_only=true`;
- `shadow_only=true`;
- `runtime_mode=paper`;
- `live_trading_enabled=false`;
- `order_submission_enabled=false`;
- `real_order_submission_enabled=false`;
- `exchange_private_access=false`.

## Artefatos Runtime

Contratos e relatorios padrao sao runtime:

```text
data/models/shadow/ai_shadow_feature_contract.json
data/reports/ai_shadow_inference_guard_report.json
```

Eles nao devem ser versionados. Arquivos em `data/`, `models/`, `reports/`,
parquet, sqlite, csv, xlsx, logs e evidence permanecem fora do git.

## Garantias De Seguranca

Este fluxo e exclusivamente paper/shadow:

- nao habilita live trading;
- nao habilita `ORDER_SUBMISSION_ENABLED`;
- nao habilita `REAL_ORDER_SUBMISSION_ENABLED`;
- nao acessa exchange privada;
- nao envia ordens;
- nao altera Freqtrade DB;
- nao altera `trades_master`;
- nao altera `training_dataset.parquet`;
- nao altera signal producer;
- nao altera runtime Qlib;
- nao altera Docker;
- nao altera `.env`.
