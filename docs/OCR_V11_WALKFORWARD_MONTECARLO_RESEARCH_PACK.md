# OCR V1.1 — Walk-forward + Monte Carlo Research Pack

## Objetivo

Esta branch implementa a terceira camada de pesquisa da Master OCR V1.1: validação temporal por walk-forward e stress estatístico por Monte Carlo sobre os outcomes simulados pela Branch 02.

A branch é research-only.

## Escopo

Inclui:

- walk-forward temporal;
- embargo entre treino e teste;
- purging por linhas de embargo;
- comparação candidato vs resultado original;
- Monte Carlo determinístico com seed;
- shuffle, bootstrap e block bootstrap;
- drawdown;
- risco de ruína;
- relatório técnico JSON;
- relatório executivo Markdown.

Não inclui:

- treino Qlib;
- alteração de Freqtrade;
- alteração de RiskManager;
- IA Shadow incremental;
- SQLite;
- live;
- ordem real;
- exchange privada;
- promoção automática.

## Entradas

Entrada padrão:

data/research/ocr_v11_trade_outcome_simulation.parquet

Esse artefato é gerado pela Branch 02 com:

python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --write --json

## Saídas runtime

Geradas somente com --write:

data/research/ocr_v11_walkforward_results.parquet
data/research/ocr_v11_monte_carlo_distribution.parquet
data/reports/ocr_v11_walkforward_montecarlo_summary.json
data/reports/training_reports/ocr_v11_walkforward_montecarlo_executive.md
data/reports/training_reports/ocr_v11_walkforward_montecarlo_summary.json

Esses arquivos não devem ser versionados.

## CLI

No-write:

python .\scripts\run_ocr_v11_walkforward_montecarlo_research.py --project-root . --no-write --json

Write:

python .\scripts\run_ocr_v11_walkforward_montecarlo_research.py --project-root . --write --json

## Critério de decisão

A estratégia candidata permanece bloqueada se:

- não supera o resultado original no walk-forward;
- ou excede o limite de risco de ruína no Monte Carlo.

Status blocked neste contexto não é erro de execução. É gate quantitativo conservador.

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
runs_training=false
updates_freqtrade=false
updates_qlib_runtime=false
auto_promote=false

## Validações

python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_ocr_v11_walkforward_montecarlo_research.py -q
python -m pytest .\tests\test_ocr_v11_tp_sl_grid_simulator.py -q
python -m pytest .\tests\test_ocr_v11_research_dataset.py -q
python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --write --json
python .\scripts\run_ocr_v11_walkforward_montecarlo_research.py --project-root . --no-write --json
python .\scripts\run_ocr_v11_walkforward_montecarlo_research.py --project-root . --write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
git diff --check
git status -sb
git status --short

## Interpretação executiva

A Branch 03 responde se a estratégia candidata sobrevive fora da amostra e sob stress.

Se o resultado for blocked, isso não invalida o projeto. Significa que o sistema corretamente impediu promoção de uma estratégia fraca.
