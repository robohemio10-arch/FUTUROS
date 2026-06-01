# Qlib Paper Refresh Supervisor

## Objetivo

O supervisor renova, em modo paper/shadow-only, a cadeia operacional usada pelo dashboard e pela Phase13:

1. market features Qlib;
2. fresh predictions;
3. active signals da Phase13;
4. relatório consolidado de freshness e stale gaps.

Ele não usa API privada da exchange, não chama Freqtrade API, não altera DB paper e não envia ordens. Os outputs são arquivos locais de features, predictions, sinais e relatórios.

## Comando

Execução única, padrão seguro:

```powershell
python .\scripts\run_qlib_paper_refresh_supervisor.py --once
```

Loop supervisionado:

```powershell
python .\scripts\run_qlib_paper_refresh_supervisor.py --interval-seconds 900
```

Sem `--interval-seconds`, o CLI executa apenas uma vez.

## Runtime Service

O `docker-compose.paper.yml` inclui o serviço dedicado:

```text
qlib-refresh-supervisor-paper
```

Comando executado:

```text
python scripts/run_qlib_paper_refresh_supervisor.py --interval-seconds 300
```

O serviço reutiliza a imagem SmartCrypto, monta apenas `data/`, `config/`, `scripts/` e `smartcrypto/`, e não monta `freqtrade/user_data`, o named volume do SQLite paper nem qualquer caminho do DB operacional do Freqtrade.

Flags de segurança fixas no serviço:

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
```

O script operacional `paper_controlado_operacao/START_PAPER_24H.ps1` já chama `docker compose -f docker-compose.paper.yml up -d`; portanto o supervisor sobe junto com os demais serviços paper, sem execução duplicada manual. Para uma renovação pontual fora do serviço, use o modo `--once`.

## Relatório

O relatório consolidado é escrito em:

```text
data/reports/qlib_paper_refresh_supervisor_report.json
```

Campos principais:

- `market_features_status`
- `predictions_status`
- `phase13_status`
- `input_data_status`
- `prediction_freshness`
- `signals_after`
- `next_recommended_run_seconds`

## Status Controlados

- `ok`: refresh completo e predições frescas.
- `blocked`: guard de segurança bloqueou o ciclo.
- `market_features_failed`: refresh de market features falhou.
- `predictions_failed`: fresh predictions falhou.
- `phase13_failed`: Phase13 falhou ao gerar sinais.
- `stale_after_refresh`: refresh terminou, mas freshness ainda ficou stale/inválido.

## Segurança

O supervisor sempre reporta:

- `runtime_mode=paper`;
- `shadow_only=true`;
- `live_trading_enabled=false`;
- `order_submission_enabled=false`;
- `real_order_submission_enabled=false`;
- `exchange_private_access=false`.

Se `LIVE_ENABLED`, `ORDER_SUBMISSION_ENABLED` ou `REAL_ORDER_SUBMISSION_ENABLED` estiverem true no ambiente, o supervisor retorna `blocked` antes de executar qualquer etapa.

## Limitações

O refresh de features pode usar fonte pública de candles quando habilitado. Isso não equivale a acesso privado nem a ordem real. O supervisor apenas reduz a dependência de execução manual; ele não muda política de risco, não liga live e não substitui validação de freshness no dashboard ou na Phase13.
