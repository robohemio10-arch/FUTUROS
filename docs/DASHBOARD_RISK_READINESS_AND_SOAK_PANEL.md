# Dashboard Risk Readiness e Soak Paper/Shadow

Esta frente adiciona um painel read-only para readiness operacional e soak paper/shadow do FUTUROS/SmartCrypto. Ele consolida sinais de operação simulada, incidentes, duplicatas, divergências, stale data, kill switch, backup/restore/offsite, runtime mode e critérios mínimos antes de qualquer avanço operacional.

## Módulo

- `smartcrypto/dashboard/risk_readiness_soak_panel.py`

A função `load_risk_readiness_soak_state()` faz a leitura e agregação estruturada. A função `render_risk_readiness_soak_panel(st)` apenas renderiza no Streamlit e não mantém estado crítico em `session_state`.

Como o critério de saída desta branch limita os arquivos alterados, a integração direta no `smartcrypto/dashboard/app.py` fica documentada. Para integrar, importe `render_risk_readiness_soak_panel` e adicione uma página nova no seletor do dashboard.

## Fontes lidas

O painel tenta ler:

- `data/reports/paper_soak_report.json`
- `data/reports/paper_session_report.json`
- `data/reports/ai_governance_dashboard_sources_report.json`
- `data/reports/data_quality_report.json`
- `data/reports/dataset_manifest.json`
- `data/reports/phase23_anti_leakage_report.json`
- `data/reports/monte_carlo_risk_simulation_report.json`
- `data/reports/event_driven_backtest_report.json`
- `data/runtime/kill_switch.json`
- `data/runtime/active_freqtrade_signals.json`
- `data/runtime/freqtrade_signal_decisions.jsonl`

Arquivos ausentes são tratados como `missing`, sem exceção fatal.

## Status

O payload retorna:

- `ok`: readiness sem bloqueios críticos.
- `warning`: avisos não críticos.
- `blocked`: algum gate crítico bloqueia readiness.
- `missing_data`: fontes necessárias ainda não foram geradas.

Modos operacionais destacados:

- `PAPER`
- `SHADOW`
- `LIVE_LOCKED`
- `PANIC`
- `RECONCILING`
- `STALE_DATA`
- `MISSING_DATA`

## Condições bloqueantes

O painel bloqueia quando detectar live ou order submission, acesso privado à exchange, tentativas de ordem shadow/live controlada, duplicatas, estados desconhecidos, divergências, incidentes P0/P1, kill switch ativo sem classificação clara, backup/restore obrigatório sem `pass`, dias paper abaixo do mínimo ou stale data acima do limite.

## Inspector CLI

```powershell
python scripts/inspect_risk_readiness_soak_sources.py `
  --required-paper-days 7 `
  --max-stale-signal-age-seconds 900
```

Saída runtime:

- `data/reports/risk_readiness_soak_dashboard_sources_report.json`

Esse relatório não deve ser versionado.

## Garantias read-only

O painel não ativa/desativa kill switch, não altera risco, não inicia ou pausa bot, não altera config, não altera registry/modelos, não envia ordens, não importa `ccxt`, não acessa exchange privada e não escreve em `data/runtime`, `data/reports`, `data/models` ou DB do Freqtrade.

Flags esperadas:

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`

Nenhum arquivo em `data/`, `models/`, `reports/`, parquet, SQLite, CSV, XLSX, logs ou evidence deve ser versionado.
