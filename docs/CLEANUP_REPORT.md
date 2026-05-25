# Relatório de limpeza — FUTUROS_CLEAN_CORE

## Mantido

- `smartcrypto/` — pacote principal.
- `scripts/` — scripts operacionais e quantitativos.
- `config/` — configurações paper, risco, Qlib, universo e sinal.
- `docker/` e `docker-compose.paper.yml` — infraestrutura paper.
- `freqtrade/user_data/config*.json` e `freqtrade/user_data/strategies/` — configuração e estratégia mínima.
- `paper_controlado_fase_05/` — importação incremental de trades Excel/OCR.
- `paper_controlado_operacao/` — operação paper.
- `tests/` — contratos e regressão.
- `docs/` essenciais e runbook atual.

## Removido do pacote limpo

- `.env` real. Usar `.env.example`.
- Pastas antigas de fases `paper_controlado_fase_01` a `04`, `06` a `22`, exceto Fase 05.
- Hotfixes antigos `paper_controlado_hotfix_*`.
- Documentos históricos/hotfixes que não são caminho operacional atual.
- Arquivos de dados `.xlsx`, `.csv`, `.parquet`, `.sqlite`, logs e evidências.

## Adicionado

- `scripts/run_trade_monte_carlo_10_workers.py`.
- `scripts/run_trade_block_monte_carlo_10_workers.py`.
- `scripts/run_paper_risk_sizing_simulation.py`.
- `scripts/run_paper_risk_controller_live.py`.
- `docs/CURRENT_OPERATION_RUNBOOK.md`.
- `docs/EXTERNAL_MODULES_REQUIRED.md`.
