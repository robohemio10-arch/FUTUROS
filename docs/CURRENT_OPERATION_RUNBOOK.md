# FUTUROS — Runbook operacional atual

## Estado aprovado

- Modo atual: `paper/research/shadow`.
- Live trading permanece bloqueado.
- Melhor política paper offline atual: `btc_075_eth_100`.
- BTCUSDT usa multiplicador `0.75`.
- ETHUSDT usa multiplicador `1.00`.
- Coletor 15s fica em projeto separado: `bitradex_realtime_candle_collector_v1` e precisa ser preservado fora deste ZIP limpo se quiser continuar acumulando candles.

## Comandos principais

```powershell
cd "C:\Smart Cripto\FUTUROS"
docker compose -f docker-compose.paper.yml up -d
docker compose -f docker-compose.paper.yml ps
```

## Importar novos trades

```powershell
.\paper_controlado_fase_05\RUN_PHASE5_PREFLIGHT.ps1
.\paper_controlado_fase_05\RUN_PHASE5_IMPORT_TRADES.ps1
.\paper_controlado_fase_05\RUN_PHASE5_REBUILD_DATASETS.ps1
.\paper_controlado_fase_05\RUN_PHASE5_VERIFY_OUTPUTS.ps1
```

## Monte Carlo

```powershell
docker exec futuros-smartcrypto-bot-paper-1 python /app/scripts/run_trade_monte_carlo_10_workers.py --workers 10 --iterations 20000
docker exec futuros-smartcrypto-bot-paper-1 python /app/scripts/run_trade_block_monte_carlo_10_workers.py --workers 10 --iterations 20000 --block-sizes 5 10 20 50
```

## Risk sizing / shadow controller

```powershell
docker exec futuros-smartcrypto-bot-paper-1 python /app/scripts/run_paper_risk_sizing_simulation.py
docker exec futuros-smartcrypto-bot-paper-1 python /app/scripts/run_paper_risk_controller_live.py
```

## Proibido nesta fase

- Não habilitar `LIVE_ENABLED`.
- Não habilitar `ORDER_SUBMISSION_ENABLED`.
- Não habilitar `REAL_ORDER_SUBMISSION_ENABLED`.
- Não usar chaves reais.
- Não usar `model_baseline.py` antigo como evidência operacional.
