# SMART FUTUROS Command Center V2 - Streamlit Pages

## Objetivo

Esta Branch 3 materializa o **SMART FUTUROS Command Center** como uma interface Streamlit
read-only. O nome documental é **SMART FUTUROS Institutional Dashboard**.

Ela sucede os contratos da Branch 1 e os builders da Branch 2. A UI não conhece fontes
operacionais brutas: consome exclusivamente snapshots JSON previamente gerados.

## Arquitetura Snapshot-First

O processo externo de snapshots produz arquivos em `data/reports`. O Streamlit usa
`page_snapshot_loader.py`, `dashboard_snapshot_service.py` e o readonly guard para ler e
validar esses arquivos. A UI não chama builders, não gera snapshots e não escreve arquivos.

## Páginas

| Página | Snapshot |
| --- | --- |
| 01. Infraestrutura | `dashboard_infrastructure_snapshot.json` |
| 02. Portfólio e Risco | `dashboard_portfolio_risk_snapshot.json` |
| 03. Grid Spot Monitor | `dashboard_grid_monitor_snapshot.json` |
| 04. Oportunidades | `dashboard_opportunity_scanner_snapshot.json` |
| 05. IA / Qlib Governance | `dashboard_ai_governance_snapshot.json` |
| 06. Controles Ativos | `dashboard_active_controls_snapshot.json` |
| 07. Relatórios Quantitativos & TCA | `dashboard_quantitative_reports_snapshot.json` |
| 08. Alertas & Mensageria | `dashboard_alerts_messaging_snapshot.json` |

O shell principal lê somente `dashboard_global_status_snapshot.json` e
`dashboard_snapshot_build_summary.json`.

## Segurança

- PAPER / SHADOW ONLY.
- Live trading e submissão real de ordens permanecem bloqueados.
- Sem exchange privada, ccxt ou OrderManager na UI.
- Sem Telegram ou NTFY direto.
- Sem alteração de risco, configuração, modelos, registry ou active signals.
- N2 e N3 são apenas stubs visuais; N4 permanece `HARD_BLOCKED`.
- A fonte financeira é o snapshot, nunca `session_state`.

Quando um snapshot está ausente, inválido ou possui schema incompatível, a página exibe
`UNKNOWN` e uma orientação operacional. Ela não tenta reconstruir o arquivo.

## Execução Local

```powershell
streamlit run smartcrypto/dashboard/app.py
```

Os snapshots devem ser produzidos fora da UI antes da abertura do dashboard.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_dashboard_streamlit_pages_readonly_v2.py tests/test_dashboard_streamlit_pages_snapshot_contract_v2.py tests/test_dashboard_streamlit_pages_static_safety_v2.py tests/test_dashboard_streamlit_app_readonly_v2.py -q
python -m pytest -q
```

## Fora do Escopo

Esta branch não implementa tema visual final, comandos reais, dispatcher real, alertas de
rede, treino ou promoção de modelo, reconstrução de datasets nem integração privada.

A próxima branch entrega stubs governados de controles e alertas. A Branch 5 aplicará o
tema visual institucional definitivo.
