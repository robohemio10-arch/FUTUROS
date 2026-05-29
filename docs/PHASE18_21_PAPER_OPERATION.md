# Fases 18–21 — Operação Paper Contínua

## Fase 18 — Paper Session Orchestrator

Cria o orquestrador para rodar sessões paper de 2h, 6h, 24h ou 7 dias, chamando geração de sinais, Freqtrade, coleta de feedback, rebuild de datasets e evidência.

## Fase 19 — Dashboard Analytics

Refatora o dashboard Streamlit para acompanhar sinais, Qlib, Freqtrade, trades paper, performance, datasets, logs, risco e evidências.

## Fase 20 — Risk Manager + Kill Switch

Adiciona `smartcrypto/risk/risk_manager.py` e comandos para ativar/desativar kill switch em paper.

## Fase 21 — Qlib Walk-forward Evaluation

Executa avaliação walk-forward do dataset Qlib-compatible, compara contra baseline e gera relatório/figuras para dashboard.

## Uso principal

```powershell
cd "E:\FUTUROS"
.\paper_controlado_operacao\START_PAPER_24H.ps1
```

