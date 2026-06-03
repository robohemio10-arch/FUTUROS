# Dashboard AI Governance e Model Registry

Esta frente adiciona um painel read-only de governança IA para o Streamlit do FUTUROS/SmartCrypto. O painel consolida Model Registry, champion/challengers, promotion gate, trainer incremental IA Shadow, drift, outcomes, avaliação financeira, Fase 23 anti-leakage, Monte Carlo, backtest event-driven, data quality, dataset manifest e a última decisão shadow.

## Escopo

O módulo principal é:

- `smartcrypto/dashboard/ai_governance_panel.py`

Ele separa a agregação de dados da camada Streamlit. A função `load_ai_governance_panel_state()` retorna um payload estruturado e testável. A função `render_ai_governance_panel(st)` apenas renderiza esse estado no Streamlit.

Como a branch mantém o critério de saída limitado aos arquivos novos, a integração direta no `smartcrypto/dashboard/app.py` fica documentada para uma etapa posterior. Para integrar, importe `render_ai_governance_panel` e adicione uma página read-only no seletor do dashboard.

## Fontes lidas

O painel tenta ler, quando existirem:

- `data/models/registry/model_registry.json`
- `data/reports/ai_shadow_incremental_trainer_report.json`
- `data/reports/model_registry_promotion_gate_report.json`
- `data/reports/ai_shadow_drift_monitor_report.json`
- `data/reports/ai_shadow_model_outcomes_report.json`
- `data/reports/ai_shadow_financial_threshold_evaluation_report.json`
- `data/reports/phase23_anti_leakage_report.json`
- `data/reports/monte_carlo_risk_simulation_report.json`
- `data/reports/event_driven_backtest_report.json`
- `data/reports/data_quality_report.json`
- `data/reports/dataset_manifest.json`
- `data/reports/ai_shadow_model_decisions.jsonl`

Arquivos ausentes são classificados como `missing`, sem exceção fatal.

## Status agregado

O painel retorna:

- `ok`: fontes disponíveis sem bloqueios.
- `warning`: fontes presentes com alertas não bloqueantes.
- `missing_data`: fontes ainda não geradas.
- `blocked`: algum artefato ou flag de segurança impede confiança operacional.

O status fica `blocked` quando qualquer fonte indicar live/order/private access, `auto_promote=true`, `promotion_allowed=true` sem gate formal aprovado, drift bloqueado, anti-leakage bloqueado ou data quality bloqueado.

## Inspector CLI

O CLI gera um relatório runtime para inspecionar as fontes do painel:

```powershell
python scripts/inspect_ai_governance_dashboard_sources.py
```

Saída padrão:

- `data/reports/ai_governance_dashboard_sources_report.json`

Esse arquivo é runtime e não deve ser versionado.

O CLI aceita overrides para todas as fontes:

```powershell
python scripts/inspect_ai_governance_dashboard_sources.py `
  --registry data/models/registry/model_registry.json `
  --trainer-report data/reports/ai_shadow_incremental_trainer_report.json `
  --promotion-report data/reports/model_registry_promotion_gate_report.json `
  --drift-report data/reports/ai_shadow_drift_monitor_report.json `
  --outcomes-report data/reports/ai_shadow_model_outcomes_report.json `
  --financial-report data/reports/ai_shadow_financial_threshold_evaluation_report.json `
  --anti-leakage-report data/reports/phase23_anti_leakage_report.json `
  --monte-carlo-report data/reports/monte_carlo_risk_simulation_report.json `
  --backtest-report data/reports/event_driven_backtest_report.json `
  --data-quality-report data/reports/data_quality_report.json `
  --dataset-manifest data/reports/dataset_manifest.json `
  --decisions-jsonl data/reports/ai_shadow_model_decisions.jsonl
```

## Garantias read-only

O painel não promove modelos, não treina, não altera thresholds, não altera risk manager, não escreve registry, não escreve modelos, não toca no Freqtrade DB, não envia ordens, não importa `ccxt` e não acessa exchange privada.

Flags esperadas:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`

Nenhum arquivo em `data/`, `models/`, `reports/`, parquet, SQLite, CSV, XLSX, logs ou evidence deve ser versionado.
