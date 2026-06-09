# Notificações críticas por ntfy e Telegram

Esta frente adiciona entrega externa de alertas críticos para smartphone sem alterar execução, risco, IA, Freqtrade, datasets ou qualquer artefato financeiro operacional.

## Módulos adicionados

- `smartcrypto/ops/notification_channels.py`
- `scripts/run_critical_notification_dispatch.py`
- `config/critical_notifications.example.yml`
- `tests/test_notification_channels.py`

## Modelo operacional

Fluxo seguro:

```text
FinancialEventLog JSONL
→ CriticalAlerting report JSON
→ NotificationDispatcher
→ ntfy / Telegram
```

A camada de notificação lê apenas:

- `data/reports/critical_alerting_report.json`

E pode escrever apenas relatório runtime de entrega:

- `data/reports/critical_notification_dispatch_report.json`

Esse relatório não deve ser versionado.

## Segurança preservada

Flags institucionais fixas nos resultados de entrega:

```text
paper_only=true
shadow_only=true
runtime_mode=paper
live_trading_enabled=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
```

O dispatcher não acessa exchange privada, não lê conta real, não envia ordens, não altera RiskManager, não altera signal producer, não escreve em SQLite Freqtrade, não altera parquet, não promove modelo e não mexe em `.env`.

## Configuração por ambiente

As credenciais devem ficar somente no `.env` local ou no secret manager da VPS. Não versionar valores reais.

```env
SMARTCRYPTO_NTFY_ENABLED=false
SMARTCRYPTO_NTFY_SERVER_URL=https://ntfy.sh
SMARTCRYPTO_NTFY_TOPIC=
SMARTCRYPTO_NTFY_TOKEN=
SMARTCRYPTO_NTFY_USERNAME=
SMARTCRYPTO_NTFY_PASSWORD=

SMARTCRYPTO_TELEGRAM_ENABLED=false
SMARTCRYPTO_TELEGRAM_BOT_TOKEN=
SMARTCRYPTO_TELEGRAM_CHAT_ID=
SMARTCRYPTO_TELEGRAM_API_BASE_URL=https://api.telegram.org
SMARTCRYPTO_TELEGRAM_PARSE_MODE=
SMARTCRYPTO_TELEGRAM_DISABLE_NOTIFICATION=false
SMARTCRYPTO_NOTIFICATION_TIMEOUT_SECONDS=10
```

## ntfy no smartphone

1. Instalar o app ntfy no smartphone.
2. Criar/assinar um tópico privado e difícil de adivinhar.
3. Configurar o mesmo tópico em `SMARTCRYPTO_NTFY_TOPIC`.
4. Ativar `SMARTCRYPTO_NTFY_ENABLED=true` apenas no `.env` local/VPS.
5. Se usar servidor ntfy protegido, configurar `SMARTCRYPTO_NTFY_TOKEN` ou `SMARTCRYPTO_NTFY_USERNAME` + `SMARTCRYPTO_NTFY_PASSWORD`.

## Telegram

1. Criar o bot no BotFather.
2. Obter o token do bot.
3. Iniciar conversa com o bot ou adicioná-lo ao grupo/canal permitido.
4. Obter o `chat_id` autorizado.
5. Configurar `SMARTCRYPTO_TELEGRAM_BOT_TOKEN` e `SMARTCRYPTO_TELEGRAM_CHAT_ID` fora do Git.
6. Ativar `SMARTCRYPTO_TELEGRAM_ENABLED=true` apenas no ambiente local/VPS.

Por segurança, o módulo usa texto simples por padrão. `SMARTCRYPTO_TELEGRAM_PARSE_MODE` fica vazio para evitar falhas por markup inválido.

## Execução

Primeiro gerar o relatório local de alertas:

```powershell
python scripts/run_financial_event_log_audit.py `
  --event-log data/reports/financial_event_log.jsonl `
  --alert-report data/reports/critical_alerting_report.json `
  --max-risk-rejections 5 `
  --max-prediction-stale 3 `
  --strict
```

Depois disparar notificações:

```powershell
python scripts/run_critical_notification_dispatch.py `
  --alert-report data/reports/critical_alerting_report.json `
  --dispatch-report data/reports/critical_notification_dispatch_report.json
```

Teste sem rede:

```powershell
python scripts/run_critical_notification_dispatch.py `
  --alert-report data/reports/critical_alerting_report.json `
  --dispatch-report data/reports/critical_notification_dispatch_report.json `
  --dry-run
```

## Política de envio

Por padrão, o dispatcher envia quando o relatório está em:

- `blocked`
- `warning`
- `missing_data`

Ele ignora status `ok`, salvo quando chamado com:

```powershell
--include-ok
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_notification_channels.py tests/test_financial_event_log_and_alerting.py
```

## Próxima integração recomendada

Adicionar chamada opcional ao `scripts/run_critical_notification_dispatch.py` dentro do supervisor operacional paper, depois do `run_financial_event_log_audit.py`, mantendo:

```text
SMARTCRYPTO_NTFY_ENABLED=false
SMARTCRYPTO_TELEGRAM_ENABLED=false
```

como padrão no repositório.
