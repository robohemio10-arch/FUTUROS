# AI Unified Feature Contract and Dataset Manifest

## Objetivo

Esta branch cria uma barreira institucional antes de qualquer treino real Qlib
ou IA Shadow. O contrato impede treino com schema instável, ordem de features
não determinística, leakage de outcome, `future_ret_*` como feature, dataset sem
hash ou linhagem pouco auditável.

O processo é read-only por padrão. Ele não treina, não promove modelo, não cria
registry, não altera Freqtrade, não altera RiskManager, não altera signals e não
envia ordens.

## Fontes permitidas

O builder audita fontes locais existentes:

- `data/feedback/outcome_events.parquet`
- `data/feedback/paper_closed_trades_incremental.parquet`
- `data/feedback/training_microbatches/*.parquet`
- `data/reports/paper_autolearning_foundation_summary.json`
- `data/reports/paper_feedback_master_consolidation_preview_v1.json`
- `data/trades/trades_master.parquet`
- `data/trades/trades_master.xlsx`

O dataset selecionado é o primeiro dataset disponível que contém pelo menos uma
feature válida e pelo menos um label válido. Na prática, o microbatch diário é o
candidato esperado porque já possui `feature_*` e `label_*`.

## FeatureContract

Arquivo JSON, somente com `--write`:

```text
data/reports/ai_unified_feature_contract_v1.json
```

Arquivo Markdown, somente com `--write`:

```text
data/reports/ai_unified_feature_contract_v1.md
```

Papéis inferidos:

- `feature`
- `label`
- `outcome`
- `metadata`
- `identifier`
- `forbidden`

Ordem de features:

```text
deterministic_feature_order=true
```

As features são ordenadas de forma determinística antes de gerar o hash.

## DatasetManifest

Arquivo JSON, somente com `--write`:

```text
data/reports/ai_unified_dataset_manifest_v1.json
```

Arquivo Markdown, somente com `--write`:

```text
data/reports/ai_unified_dataset_manifest_v1.md
```

O manifest registra:

- hashes das fontes;
- hash determinístico do dataset selecionado;
- contagem de linhas/colunas;
- tipos;
- null counts;
- janela temporal;
- símbolos;
- sides;
- distribuição de labels;
- linhagem.

## Política anti-leakage

Nunca usar como feature:

- `future_ret_*`
- `target_*`
- `label_*`
- `outcome_*`
- `net_pnl`
- `gross_pnl`
- `profit_ratio`
- `exit_reason`
- `exit_price`
- `close_time`
- `close_time_utc`
- `liquidation_flag`
- `roi_hit`
- `stoploss_hit`
- `forced_exit`
- `qlib_prediction_id`
- `ai_shadow_decision_id`

Essas colunas podem aparecer no dataset como label, outcome, metadata,
identifier ou forbidden, mas não entram em `feature_columns`.

## CLI

Preview no-write:

```powershell
python .\scripts\build_ai_unified_feature_contract_v1.py --project-root . --json
```

Escrever relatórios:

```powershell
python .\scripts\build_ai_unified_feature_contract_v1.py --project-root . --write --json
```

## Garantias de segurança

Sempre preservado:

- `paper_only=true`
- `shadow_only=true`
- `training_requested=false`
- `qlib_training_performed=false`
- `ai_shadow_training_performed=false`
- `registry_write_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false`
- `writes_sqlite=false`

## Fora de escopo

Esta branch não implementa:

- treino real Qlib;
- treino real IA Shadow;
- triple-barrier;
- walk-forward/embargo;
- registry;
- promoção de modelo;
- alterações em Freqtrade, RiskManager, signal producer, Docker, `.env` ou
  configs live.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_ai_unified_feature_contract_dataset_manifest_v1.py -q
python .\scripts\build_ai_unified_feature_contract_v1.py --project-root . --json
python .\scripts\build_ai_unified_feature_contract_v1.py --project-root . --write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git diff --cached --check
git status --short
```
