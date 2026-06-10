# Trade Event Notifications — NTFY + Telegram + Daemon Permanente

## Objetivo

Implementar notificações de eventos de trade paper/shadow para NTFY e Telegram com:

- daemon polling read-only;
- baseline seguro de histórico;
- idempotência por evento e por canal;
- retry parcial sem duplicar canal já entregue;
- serviço Docker permanente protegido por profile;
- preservação total dos invariantes paper/shadow.

## Branch

codex/trade-event-notifications-service-and-channel-idempotency

## Arquivos alterados

- smartcrypto/ops/trade_event_notifications.py
- scripts/run_trade_event_notifications.py
- tests/test_trade_event_notifications.py
- docker-compose.paper.yml
- docs/TELEGRAM_TRADE_EVENT_NOTIFICATIONS_BINANCE_LINKS.md
- PROJECT_MANIFEST_CLEAN.json

## Eventos suportados

- OPEN_LONG
- OPEN_SHORT
- CLOSE_LONG
- CLOSE_SHORT

## Pares monitorados

- BTC/USDT:USDT -> https://www.binance.com/en/futures/BTCUSDT
- ETH/USDT:USDT -> https://www.binance.com/en/futures/ETHUSDT

## Canais suportados

- telegram
- ntfy
- all

## Idempotência

A versão anterior persistia o evento completo por:

- notification_key = trade_id:event_type

A versão atual preserva compatibilidade com essa tabela legada e adiciona idempotência por canal:

- notification_key + channel

Tabela legada:

- trade_event_notifications

Tabela nova:

- trade_event_notification_channels

Efeito operacional:

- Se NTFY entrega e Telegram falha, NTFY fica marcado como entregue.
- No retry, apenas Telegram é tentado.
- Se Telegram entrega e NTFY falha, Telegram fica marcado como entregue.
- No retry, apenas NTFY é tentado.
- O evento só entra como completo quando todos os canais requeridos do modo selecionado estiverem entregues.
- Registros legados com status sent, baseline ou dry_run continuam sendo tratados como completos para backward compatibility.

## Baseline

O baseline deve ser executado antes de iniciar o daemon real.

Comando canônico:

docker compose -f ".\docker-compose.paper.yml" run --rm --no-deps phase14-feedback-sync-paper `
  python scripts/run_trade_event_notifications.py `
  --source-db /paper-db/tradesv3.paper.sqlite `
  --baseline `
  --channels all

Efeito:

- Marca todo o histórico conhecido como baseline.
- Não envia NTFY.
- Não envia Telegram.
- Previne avalanche de mensagens antigas.
- Popula a tabela legada e a tabela por canal.

## Daemon manual

Uso operacional temporário:

docker compose -f ".\docker-compose.paper.yml" run --rm --no-deps `
  -e SMARTCRYPTO_NTFY_ENABLED `
  -e SMARTCRYPTO_NTFY_SERVER_URL `
  -e SMARTCRYPTO_NTFY_TOPIC `
  -e SMARTCRYPTO_NTFY_TOKEN `
  -e SMARTCRYPTO_NTFY_USERNAME `
  -e SMARTCRYPTO_NTFY_PASSWORD `
  -e SMARTCRYPTO_TELEGRAM_ENABLED `
  -e SMARTCRYPTO_TELEGRAM_BOT_TOKEN `
  -e SMARTCRYPTO_TELEGRAM_CHAT_ID `
  -e SMARTCRYPTO_TELEGRAM_API_BASE_URL `
  -e SMARTCRYPTO_TELEGRAM_PARSE_MODE `
  -e SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION `
  -e SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS `
  phase14-feedback-sync-paper `
  python scripts/run_trade_event_notifications.py `
  --source-db /paper-db/tradesv3.paper.sqlite `
  --daemon `
  --send-real `
  --channels all `
  --poll-seconds 5

## Serviço Docker permanente

Serviço adicionado:

- trade-event-notifications-paper

Profile:

- notifications

O serviço não sobe em um `docker compose up -d` comum.

Comando para iniciar explicitamente:

docker compose -f ".\docker-compose.paper.yml" --profile notifications up -d trade-event-notifications-paper

Comando para parar:

docker compose -f ".\docker-compose.paper.yml" --profile notifications stop trade-event-notifications-paper

Comando para logs:

docker compose -f ".\docker-compose.paper.yml" --profile notifications logs -f trade-event-notifications-paper

## Configuração do serviço

O serviço usa:

- build: docker/smartcrypto/Dockerfile
- restart: unless-stopped
- env_file: .env
- volume read-only para /paper-db
- state oficial em /app/data/runtime/trade_event_notifications.sqlite
- report oficial em /app/data/reports/trade_event_notifications_report.json
- source DB: /paper-db/tradesv3.paper.sqlite
- channels default: all
- poll default: 5 segundos

Variáveis opcionais:

- SMARTCRYPTO_TRADE_EVENT_NOTIFICATIONS_CHANNELS=all
- SMARTCRYPTO_TRADE_EVENT_NOTIFICATIONS_POLL_SECONDS=5

## Validação de ambiente sanitizada

Nunca imprimir tokens, chat_id integral, tópico privado ou qualquer segredo.

Inspecionar apenas presença e tamanho:

python -c "import os,json; print(json.dumps({'ntfy_enabled': os.getenv('SMARTCRYPTO_NTFY_ENABLED'), 'ntfy_topic_present': bool(os.getenv('SMARTCRYPTO_NTFY_TOPIC')), 'ntfy_topic_len': len(os.getenv('SMARTCRYPTO_NTFY_TOPIC') or ''), 'telegram_enabled': os.getenv('SMARTCRYPTO_TELEGRAM_ENABLED'), 'telegram_token_present': bool(os.getenv('SMARTCRYPTO_TELEGRAM_BOT_TOKEN')), 'telegram_token_len': len(os.getenv('SMARTCRYPTO_TELEGRAM_BOT_TOKEN') or ''), 'telegram_chat_id_present': bool(os.getenv('SMARTCRYPTO_TELEGRAM_CHAT_ID')), 'telegram_chat_id_len': len(os.getenv('SMARTCRYPTO_TELEGRAM_CHAT_ID') or '')}, sort_keys=True))"

## Relatório runtime

Arquivo:

- data/reports/trade_event_notifications_report.json

Campos principais:

- status
- reason
- daemon
- daemon_iteration
- channels
- dry_run
- events_detected
- events_pending
- events_dispatched
- events_marked_sent
- dispatches
- sends_orders
- changes_risk
- exchange_private_access

O relatório é runtime e não deve ser versionado.

## State runtime

Arquivo:

- data/runtime/trade_event_notifications.sqlite

Tabelas:

- trade_event_notifications
- trade_event_notification_channels

O state é runtime e não deve ser versionado.

## Comportamento esperado

Sem evento novo:

- status=ok
- reason=no_pending_events
- events_pending=0
- events_dispatched=0
- events_marked_sent=0

Com evento novo e ambos os canais entregues:

- status=ok
- reason=processed
- events_pending=1
- events_dispatched=1
- events_marked_sent=1
- successful_channels=["ntfy", "telegram"]
- remaining_channels_after=[]

Com falha parcial:

- status=blocked
- reason=required_channel_delivery_blocked_or_failed
- canal entregue é persistido em trade_event_notification_channels
- canal pendente permanece em remaining_channels_after
- próximo ciclo tenta apenas o canal pendente

## Validações

docker compose -f ".\docker-compose.paper.yml" config

python -m compileall smartcrypto/ops/trade_event_notifications.py tests/test_trade_event_notifications.py

python -m pytest tests/test_trade_event_notifications.py tests/test_notification_channels_dashboard_test_panel.py tests/test_critical_notifications_dashboard_panel.py -q

python scripts/generate_project_manifest.py --check

python scripts/scan_versioned_secrets.py --json

## Invariantes

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

## Limites

Esta entrega não libera live, canary ou ordens reais.

O sistema continua restrito a observabilidade e comunicação paper/shadow.

## Bootstrap versionado de permissões

O serviço `trade-event-notifications-paper` usa bootstrap versionado para corrigir permissões de runtime em bind mount antes de iniciar o daemon.

Arquivo:

- scripts/docker_runtime_permissions_bootstrap.py

Motivo:

- Em Windows/Docker Desktop ou migração de VPS, arquivos em `data/reports` e `data/runtime` podem ficar graváveis no host, mas não graváveis pelo usuário `smartcrypto` dentro do container.
- O erro operacional observado foi `PermissionError: [Errno 13] Permission denied` ao tentar gravar `data/reports/trade_event_notifications_report.json`.

Modelo adotado:

- O serviço inicia com `user: "0:0"`.
- O bootstrap cria e ajusta permissões de:
  - /app/data/reports
  - /app/data/runtime
- O bootstrap aplica owner UID/GID 10001.
- Depois faz drop de privilégio para UID/GID 10001.
- O daemon real roda sem privilégio root.

Comando interno do serviço:

python scripts/docker_runtime_permissions_bootstrap.py \
  --path /app/data/reports \
  --path /app/data/runtime \
  -- \
  python scripts/run_trade_event_notifications.py \
  --source-db /paper-db/tradesv3.paper.sqlite \
  --state-db /app/data/runtime/trade_event_notifications.sqlite \
  --report /app/data/reports/trade_event_notifications_report.json \
  --daemon \
  --send-real \
  --channels ${SMARTCRYPTO_TRADE_EVENT_NOTIFICATIONS_CHANNELS:-all} \
  --poll-seconds ${SMARTCRYPTO_TRADE_EVENT_NOTIFICATIONS_POLL_SECONDS:-5}

Safety:

- O bootstrap não acessa exchange.
- O bootstrap não envia ordens.
- O bootstrap não altera risco.
- O bootstrap só ajusta permissões em diretórios runtime montados.
## Painel runtime read-only de notificações de trade

O dashboard possui uma página `Trade notifications` para monitorar o daemon permanente `trade-event-notifications-paper`.

Fonte de dados:

- `data/reports/trade_event_notifications_report.json`

O painel é estritamente read-only:

- não envia NTFY;
- não envia Telegram;
- não acessa exchange;
- não envia ordens;
- não altera risco;
- não modifica SQLite de estado.

Campos monitorados:

- `created_at`
- `daemon`
- `daemon_iteration`
- `dry_run`
- `channels`
- `events_detected`
- `events_pending`
- `events_dispatched`
- `events_marked_sent`
- `reason`
- `status`
- flags de safety

Alertas institucionais:

- `report_missing`
- `report_stale`
- `daemon_not_true`
- `dry_run_not_false`
- `channels_not_all`
- `events_pending_positive`
- `daemon_status_not_ok`
- qualquer flag unsafe de live/order/private exchange/risk

Condição operacional esperada:

daemon=true
dry_run=false
channels=all
events_pending=0
status=ok
sends_orders=false
changes_risk=false
exchange_private_access=false
