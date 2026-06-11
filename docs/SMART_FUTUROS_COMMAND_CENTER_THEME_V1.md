# SMART FUTUROS Command Center Theme V1

## Objetivo

Esta Branch 5 aplica a identidade visual institucional ao **SMART FUTUROS Command Center**.
Ela conclui a camada de apresentação construída sobre contratos, builders de snapshots,
páginas Streamlit read-only e stubs de controles/alertas das Branches 1 a 4.

## Arquitetura visual

O **SMART FUTUROS Institutional Dashboard** permanece snapshot-first. Cada página injeta o
CSS local, apresenta topbar, sidebar de ambiente, título, seções do snapshot e rodapé de
auditoria. A camada `smartcrypto.dashboard.ui` não executa builders nem conhece serviços de
exchange.

## Sistema visual

- Fundo principal `#020A12`, painéis `#071420` e cards `#081827`.
- Ciano identifica informação/read-only; verde indica OK; amarelo indica atenção; vermelho
  indica bloqueio/erro; roxo identifica IA e pesquisa.
- A família tipográfica usa Inter quando disponível e fallbacks locais do sistema.
- Tokens de cor, tipografia, espaçamento, raio, sombra e gradiente ficam em `ui/tokens.py`.
- O CSS local fica em `dashboard/assets/futuros_command_center.css`, sem CDN ou URL externa.

## Componentes

A topbar mantém visíveis `PAPER / SHADOW ONLY`, `LIVE LOCKED`,
`ORDER SUBMISSION DISABLED`, `READINESS BLOCKED` e `RISKMANAGER AUTHORITY`. A sidebar
padroniza as oito páginas e resume o ambiente. Cards, pills, tabelas, painéis, placeholders
de chart e estados empty/unknown/error usam HTML escapado. O footer registra o snapshot e
as proibições operacionais do dashboard.

## Padrão por página

1. Infraestrutura: runtime, conectividade, latência e market data.
2. Portfólio e Risco: capital, PnL, drawdown, VaR/CVaR e reconciliação.
3. Grid Spot Monitor: canal, níveis, integridade, dust e placeholder de heatmap.
4. Oportunidades: scanner read-only e governança hard-blocked de execução real.
5. IA / Qlib Governance: estado de modelo, challenger, drift e IA Shadow.
6. Controles Ativos: níveis N1-N4, Readiness & Gates e N4 HARD_BLOCKED.
7. Relatórios Quantitativos & TCA: métricas, TCA, Decision Trace e pipeline OCR/dataset.
8. Alertas & Mensageria: dispatcher e Telegram/NTFY estritamente como stubs.

## Segurança

Este tema é apenas camada visual. Ele não executa ordens, não altera risco, não chama
CommandBus real, não envia Telegram/NTFY real, não chama exchange, não promove modelos,
não executa OCR, não importa trades, não reconstrói datasets, não altera readiness, não
libera canary e não libera live.

Os componentes não leem secrets, não escrevem snapshots e não acessam conta privada. A
autoridade operacional final continua sendo o RiskManager, e N4 permanece hard-blocked.
Os componentes de apresentação preexistentes de cards, tabelas, controles e alertas foram
adaptados somente para delegar ao tema e recolher payloads extensos; seus contratos de
dry-run/stub e seus resultados de auditoria não foram alterados.

## Execução local

```powershell
streamlit run smartcrypto/dashboard/app.py
```

Em clone limpo, snapshots ausentes são exibidos como `UNKNOWN`/`MISSING_OPTIONAL`; a UI não
tenta gerá-los.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_dashboard_theme_tokens_v1.py -q
python -m pytest tests/test_dashboard_ui_components_v1.py -q
python -m pytest tests/test_dashboard_visual_contract_v1.py -q
python -m pytest tests/test_dashboard_theme_pages_snapshot_contract_v1.py -q
python -m pytest tests/test_dashboard_theme_static_safety_v1.py -q
python -m pytest -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

## Limites e próxima etapa

Esta branch não cria lógica operacional, fonte de dados, execução, mensageria ou automação.
Após a auditoria visual, a próxima etapa recomendada é o pacote de continuidade do soak
paper/shadow ou uma auditoria de cobertura semântica dos snapshots.
