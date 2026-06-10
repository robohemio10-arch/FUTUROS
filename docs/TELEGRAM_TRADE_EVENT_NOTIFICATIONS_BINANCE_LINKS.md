# Telegram Trade Event Notifications Binance Links

Objetivo:
Enviar mensagem Telegram ao abrir ou fechar posicao paper/shadow LONG ou SHORT em BTC/USDT:USDT e ETH/USDT:USDT, incluindo link direto da Binance Futures.

Branch:
codex/telegram-trade-event-notifications-binance-links

Arquivos:
- smartcrypto/ops/trade_event_notifications.py
- scripts/run_trade_event_notifications.py
- tests/test_trade_event_notifications.py
- docs/TELEGRAM_TRADE_EVENT_NOTIFICATIONS_BINANCE_LINKS.md

Eventos suportados:
- OPEN_LONG
- OPEN_SHORT
- CLOSE_LONG
- CLOSE_SHORT

Pares monitorados:
- BTC/USDT:USDT -> https://www.binance.com/en/futures/BTCUSDT
- ETH/USDT:USDT -> https://www.binance.com/en/futures/ETHUSDT

Fonte de dados:
- SQLite/snapshot paper Freqtrade
- Tabela: trades
- Acesso: read-only via PRAGMA query_only=ON
- Nao acessa exchange privada

Idempotencia:
- Chave deterministica: trade_id + event_type
- Estado local runtime: data/runtime/trade_event_notifications.sqlite
- Evita duplicidade apos restart

Relatorio runtime:
- data/reports/trade_event_notifications_report.json
- Nao deve ser versionado

Canal:
- Telegram only nesta branch
- NTFY permanece desabilitado no dispatcher desta frente
- Usa SMARTCRYPTO_TELEGRAM_* ja existentes

Mensagem:
- Evento
- Par
- Trade ID
- Lado
- Horario UTC
- Entrada
- Saida
- Stake
- PnL realizado
- Motivo saida
- Link Binance Futures
- Flags de seguranca

Invariantes:
paper_only=true
shadow_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false

Comando dry-run:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --dry-run

Comando real controlado:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --send-real

Observacao:
O envio real depende de SMARTCRYPTO_TELEGRAM_ENABLED=true, SMARTCRYPTO_TELEGRAM_BOT_TOKEN e SMARTCRYPTO_TELEGRAM_CHAT_ID configurados no ambiente. Nunca versionar tokens.

Validacao:
python -m compileall scripts/run_trade_event_notifications.py smartcrypto/ops/trade_event_notifications.py tests/test_trade_event_notifications.py
python -m pytest tests/test_trade_event_notifications.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json

Limite:
Esta branch e observabilidade/comunicacao paper-shadow. Nao libera live, canary, readiness ou envio de ordens.
