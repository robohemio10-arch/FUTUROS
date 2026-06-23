# Qlib OCR V1.1 Supervised Training Lab

## Objetivo

Esta branch implementa um laboratório supervisionado research-only para treinar e avaliar modelos candidatos sobre a base OCR V1.1.

O objetivo não é promover modelo nem gerar sinal operacional. O objetivo é medir se features derivadas da base OCR V1.1 conseguem selecionar trades de maior qualidade financeira fora da amostra.

## Escopo

Inclui:

- leitura do dataset OCR V1.1 research;
- junção com outcomes da Branch 02;
- leitura do gate da Branch 03 como metadado;
- criação de target supervisionado `target_original_win`;
- seleção segura de features numéricas;
- exclusão de colunas com vazamento;
- treinamento temporal supervisionado;
- embargo entre treino e teste;
- avaliação financeira do seletor;
- relatório técnico JSON;
- relatório executivo Markdown;
- export research-only de artefato `.joblib` em `data/models/`.

Não inclui:

- promoção de modelo;
- registro em ModelRegistry;
- alteração de Qlib runtime;
- alteração de Freqtrade;
- alteração de RiskManager;
- alteração de IA Shadow runtime;
- SQLite;
- live/canary;
- ordem real;
- exchange privada.

## Entradas

Entradas padrão:

data/research/ocr_v11_trade_research_dataset.parquet
data/research/ocr_v11_trade_outcome_simulation.parquet
data/reports/ocr_v11_walkforward_montecarlo_summary.json

A Branch 03 deve ser interpretada como gate negativo para o candidato TP/SL fixo. O resultado `DESCARTAR_CANDIDATO` não vira label aprovado.

## Saídas runtime

Geradas somente com `--write`:

data/research/qlib_ocr_v11_supervised_training_predictions.parquet
data/models/qlib_ocr_v11/research/qlib_ocr_v11_supervised_candidate.joblib
data/reports/qlib_ocr_v11_supervised_training_summary.json
data/reports/training_reports/qlib_ocr_v11_supervised_training_executive.md
data/reports/training_reports/qlib_ocr_v11_supervised_training_summary.json

Esses arquivos não devem ser versionados.

## CLI

No-write:

python .\scripts\run_qlib_ocr_v11_supervised_training_lab.py --project-root . --no-write --json

Write:

python .\scripts\run_qlib_ocr_v11_supervised_training_lab.py --project-root . --write --json

## Segurança preservada

Sempre preserva:

paper_only=true
shadow_only=true
live_trading_enabled=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
changes_model=false
runs_ocr=false
imports_ocr=false
promotes_quality_gated=false
runs_ai_shadow_incremental=false
cleans_sqlite=false
updates_freqtrade=false
updates_qlib_runtime=false
updates_risk_manager=false
registers_model=false
auto_promote=false
production_enabled=false

## Critério de leitura

Status `ok` significa que o laboratório supervisionado rodou e o seletor superou o baseline financeiro definido.

Status `warning` significa que o treino rodou, mas ainda não há evidência suficiente para avanço.

Status `blocked` significa que o laboratório falhou em dados, folds, seleção ou métrica financeira.

Nenhum desses status libera promoção automática.

## Validações

python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_qlib_ocr_v11_supervised_training_lab.py -q
python -m pytest .\tests\test_ocr_v11_walkforward_montecarlo_research.py -q
python -m pytest .\tests\test_ocr_v11_tp_sl_grid_simulator.py -q
python -m pytest .\tests\test_ocr_v11_research_dataset.py -q
python .\scripts\run_qlib_ocr_v11_supervised_training_lab.py --project-root . --no-write --json
python .\scripts\run_qlib_ocr_v11_supervised_training_lab.py --project-root . --write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
git diff --check
git status -sb
git status --short
