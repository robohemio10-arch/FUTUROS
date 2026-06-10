# Trade Event Notifications Daemon NTFY Telegram

Objetivo:
Ativar notificacoes NTFY e Telegram para eventos de trade paper/shadow, com daemon polling read-only, baseline de historico e idempotencia por trade_id + event_type.

Branch:
codex/trade-event-notifications-daemon-ntfy-telegram

Arquivos alterados:
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

Canais:
- --channels telegram
- --channels ntfy
- --channels all

Modo baseline:
Marca todos os eventos historicos atualmente detectados como conhecidos sem enviar notificacoes.
Uso obrigatorio antes de ativar daemon real, para impedir envio em massa de eventos antigos.

Comando baseline:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --baseline --channels all

Modo dry-run:
Valida deteccao, canais e payload sem chamada real de rede.
Nao persiste idempotencia, exceto se --persist-dry-run for usado explicitamente em teste.

Comando dry-run:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --dry-run --channels all --limit 1

Modo envio real controlado:
Envia notificacoes reais pelos canais selecionados.
Persistencia so ocorre quando todos os canais obrigatorios do modo selecionado retornam status sent.

Comando real controlado:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --send-real --channels all --limit 1

Modo daemon:
Executa polling continuo sobre SQLite paper em modo read-only.
Recomendado somente apos baseline concluido.

Comando daemon dry-run:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --daemon --dry-run --channels all --poll-seconds 10

Comando daemon real:
python scripts/run_trade_event_notifications.py --source-db data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite --daemon --send-real --channels all --poll-seconds 10

Estado runtime:
- data/runtime/trade_event_notifications.sqlite
- Nao versionar

Relatorio runtime:
- data/reports/trade_event_notifications_report.json
- Nao versionar
- Pode conter response_excerpt de NTFY/Telegram; nao compartilhar publicamente

Fonte de dados:
- SQLite/snapshot paper Freqtrade
- Tabela: trades
- Acesso read-only: PRAGMA query_only=ON
- Nao acessa exchange privada

Idempotencia:
- notification_key = trade_id:event_type
- Evita duplicidade apos restart
- Eventos baseline tambem bloqueiam envio posterior do historico

Requisitos de ambiente:
- SMARTCRYPTO_NTFY_ENABLED=true
- SMARTCRYPTO_NTFY_SERVER_URL=https://ntfy.sh
- SMARTCRYPTO_NTFY_TOPIC configurado
- SMARTCRYPTO_TELEGRAM_ENABLED=true
- SMARTCRYPTO_TELEGRAM_BOT_TOKEN configurado
- SMARTCRYPTO_TELEGRAM_CHAT_ID configurado
- SMARTCRYPTO_TELEGRAM_API_BASE_URL=https://api.telegram.org

Nunca versionar tokens, chat_id privado, topicos privados ou qualquer segredo.

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

Validacao:
python -m compileall scripts/run_trade_event_notifications.py smartcrypto/ops/trade_event_notifications.py tests/test_trade_event_notifications.py
python -m pytest tests/test_trade_event_notifications.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json

Limite:
Esta branch e exclusivamente observabilidade/comunicacao paper-shadow.
Nao libera live, canary, readiness ou envio de ordens.
