# Model Registry Champion/Challenger IA Shadow

## Objetivo

O Model Registry formaliza a politica champion/challenger para modelos IA
Shadow. Ele registra modelos gerados pelo trainer incremental como
`challenger`, mas nunca promove automaticamente para `champion`.

O fluxo e paper/shadow only e serve para governanca de modelos antes de qualquer
decisao operacional.

## Fontes

Entrada padrao:

```text
data/reports/ai_shadow_incremental_trainer_report.json
```

Saidas runtime:

```text
data/models/registry/model_registry.json
data/reports/model_registry_promotion_gate_report.json
```

Esses arquivos ficam em `data/` e nao devem ser versionados.

## Registro Challenger

O CLI registra o modelo do relatorio do trainer como challenger:

```powershell
python .\scripts\register_ai_shadow_challenger_model.py
```

Com parametros explicitos:

```powershell
python .\scripts\register_ai_shadow_challenger_model.py `
  --trainer-report data/reports/ai_shadow_incremental_trainer_report.json `
  --registry data/models/registry/model_registry.json `
  --report data/reports/model_registry_promotion_gate_report.json `
  --min-rows 100 `
  --min-accuracy 0.55 `
  --min-f1 0.50 `
  --min-roc-auc 0.55
```

Em modo normal, um modelo valido pode ser registrado como challenger mesmo que
falhe o gate de promocao. Nesse caso:

- `promotion_status=pending`;
- `promotion_gate_status=blocked`;
- `auto_promote=false`.

Em `--strict`, qualquer violacao do gate retorna `status=blocked`.

## Promotion Gate

A promocao fica bloqueada quando:

- `sample_warning=true`;
- `input_rows` abaixo de `--min-rows`;
- `roc_auc` ausente ou abaixo de `--min-roc-auc`, quando esse limite for
  configurado;
- `f1` abaixo de `--min-f1`;
- `accuracy` abaixo de `--min-accuracy`;
- `class_balance` indica apenas uma classe;
- flags de seguranca nao sao paper/shadow only;
- `live_trading_enabled=true`;
- `order_submission_enabled=true`;
- `real_order_submission_enabled=true`;
- `exchange_private_access=true`;
- `feature_columns` esta ausente ou vazia;
- `model_id` ou `model_version` esta ausente;
- `auto_promote=true`.

Mesmo quando nao ha violacoes, o gate retorna
`eligible_pending_manual_review`, nao promocao automatica.

## Estrutura Do Registry

O arquivo `model_registry.json` contem:

- `registry_version`;
- `updated_at_utc`;
- `champion_model_id`;
- `champion_model_version`;
- `challengers`;
- `rejected_promotions`;
- flags de seguranca paper/shadow only.

O champion existente e preservado. Registrar um challenger nao altera
`champion_model_id` nem `champion_model_version`.

## Garantias De Seguranca

Este fluxo:

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
- nao altera `.env`;
- nao promove modelo automaticamente.
