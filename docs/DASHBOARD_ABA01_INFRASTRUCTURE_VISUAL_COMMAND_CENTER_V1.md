# DASHBOARD_ABA01_INFRASTRUCTURE_VISUAL_COMMAND_CENTER_V1

## Objetivo

Esta branch transforma a Aba 01 - Infraestrutura do SMART FUTUROS Command Center em uma tela visual institucional de telemetria, preservando integralmente o contrato read-only e snapshot-first existente.

A pagina passa a funcionar como base visual para as demais abas do dashboard, sem alterar runtime, risco, modelo, dataset, OCR, Docker, Freqtrade, readiness, sinais ativos, alertas reais ou execucao de ordens.

## Escopo implementado

Arquivos alterados ou adicionados:

- smartcrypto/dashboard/pages/01_infrastructure.py
- smartcrypto/dashboard/assets/futuros_command_center.css
- smartcrypto/dashboard/ui/status.py
- smartcrypto/dashboard/ui/cards.py
- smartcrypto/dashboard/ui/charts.py
- smartcrypto/dashboard/ui/__init__.py
- tests/test_dashboard_aba01_visual_contract_v1.py
- docs/DASHBOARD_ABA01_INFRASTRUCTURE_VISUAL_COMMAND_CENTER_V1.md
- PROJECT_MANIFEST_CLEAN.json

## Contrato preservado

A Aba 01 continua usando:

- load_page_snapshot
- render_snapshot_page
- DashboardPageId.infrastructure
- data/reports/dashboard_infrastructure_snapshot.json
- EXPECTED_SCHEMA_VERSION = dashboard_infrastructure_snapshot_v1
- REQUIRED_SECTIONS
- METRICS

O bloco visual foi adicionado antes da tabela canonica, e a tabela canonica read-only permanece renderizada para preservar os testes e a rastreabilidade institucional.

## Camada visual adicionada

A pagina agora possui:

- Hero institucional de telemetria.
- Guardrails permanentes.
- Strip de KPIs operacionais.
- Grid visual da Aba 01.
- Paineis de conectividade, Redis, Docker, host, rate limits, market data health, runtime evidence, source health e eventos recentes.
- Resumo institucional das demais abas.
- Mini-SVGs e componentes CSS-only locais.
- Badges e status compativeis com a ordem de severidade institucional.

## Ordem de severidade visual

Ordem institucional aplicada:

HARD_BLOCKED > BLOCKED > CRITICAL > ERROR > WARNING > STALE > UNKNOWN > OK

Implementada em:

- smartcrypto/dashboard/ui/status.py
- status_severity_rank
- worst_status
- is_blocking_visual_status

## Componentes visuais adicionados

Em cards.py:

- render_compact_kpi
- render_health_card
- render_mini_panel_card
- render_blocked_action_card
- render_card_grid

Em charts.py:

- render_sparkline_svg
- render_latency_scatter_svg
- render_mini_donut_css
- render_mini_bar_stack
- render_grid_channel_preview
- render_depth_preview

Todos os componentes usam HTML, SVG e CSS local, sem dependencias externas, rede, runtime execution, escrita operacional ou acesso a secrets.

## Safety

A branch preserva os invariantes:

- dashboard_readonly=true
- paper_only=true
- shadow_only=true
- live_locked=true
- live_trading_enabled=false
- order_submission_enabled=false
- real_order_submission_enabled=false
- sends_orders=false
- sends_notifications=false
- changes_risk=false
- changes_model=false
- uses_ccxt=false
- exchange_private_access=false

A Aba 01 nao executa producers, nao chama APIs privadas, nao envia Telegram/NTFY, nao altera YAML, nao altera .env, nao cria ordem e nao altera readiness.

## Fonte de dados

Fonte principal da Aba 01:

data/reports/dashboard_infrastructure_snapshot.json

A pagina apenas consome snapshot ja materializado. Ausencia, stale ou blocker sao representados visualmente como UNKNOWN, STALE, WARNING, BLOCKED ou HARD_BLOCKED, sem fallback operacional artificial.

## Teste dedicado

Novo teste:

tests/test_dashboard_aba01_visual_contract_v1.py

Cobertura:

- Presenca da pagina e CSS.
- Contrato com snapshot de infraestrutura.
- Preservacao de render_snapshot_page.
- Presenca dos blocos visuais.
- Ausencia de imports e termos proibidos.
- Estabilidade da severidade visual.
- Escape HTML dos componentes.
- Garantia de que STALE nao e renderizado como OK.

## Validacoes observadas durante a branch

Auditoria semantica dashboard:

- status=ok
- 21/21 PASS
- failed_count=0

Testes dashboard:

- 236 passed

Teste dedicado da Aba 01:

- 12 passed

Build de snapshots no estado atual:

- dashboard_status=BLOCKED
- global_source_health_status=BLOCKED
- pages_blocked=7
- pages_degraded=1
- pages_ok=0

Esses bloqueios sao esperados e derivados de fontes stale/runtime readiness, nao da camada visual.

## Estado operacional esperado

A branch e visual/read-only. Ela nao busca remover blockers, nao libera readiness, nao libera canary e nao libera live.

Os blockers atuais continuam autoridade operacional ate refresh externo manual das evidencias e rebuild dos snapshots.

## Proximo passo apos merge

Apos merge na dev, a Aba 01 passa a ser a base visual canonica para replicacao progressiva nas demais abas:

1. Aba 02 - Portfolio e Risco.
2. Aba 03 - Grid Spot Monitor.
3. Aba 04 - Oportunidades.
4. Aba 05 - IA / Qlib Governance.
5. Aba 06 - Controle Ativo.
6. Aba 07 - Relatorios Quantitativos & TCA.
7. Aba 08 - Alertas & Mensageria.

A replicacao deve preservar a mesma regra: visual institucional sem alterar runtime, ordens, risco, readiness, modelo, dataset ou notificacoes reais.
