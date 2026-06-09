# Critical Notifications Dashboard Panel

## Objetivo

Adicionar observabilidade read-only para notificações críticas NTFY/Telegram no dashboard Streamlit do FUTUROS / SmartCrypto.

## Escopo

O painel lê, quando disponíveis:

- `data/reports/critical_alerting_report.json`
- `data/reports/critical_notification_dispatch_report.json`

Ele também resume a configuração efetiva dos canais a partir das variáveis de ambiente já suportadas por `smartcrypto.ops.notification_channels`, sem exibir valores sensíveis.

## Safety contract

Este painel é estritamente read-only:

- não envia notificações reais;
- não acessa exchange;
- não lê conta privada;
- não envia ordens;
- não altera risco;
- não altera `.env`;
- não escreve no DB operacional do Freqtrade;
- não habilita live;
- não habilita canary.

Flags esperadas:

```text
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
```

## Redação de segredos

O painel não deve exibir:

- token NTFY;
- topic NTFY;
- usuário/senha;
- Telegram bot token;
- Telegram chat_id;
- headers de autorização;
- URLs contendo credenciais.

Em vez disso, mostra apenas indicadores booleanos como `token_configured`, `topic_configured`, `bot_token_configured` e `chat_id_configured`.

## Estados operacionais

- `ok`: relatórios carregados, canais sem bloqueio crítico e safety flags preservadas.
- `degraded`: relatórios ausentes, canais desabilitados, configuração incompleta ou fonte inválida sem violação de safety.
- `blocked`: qualquer violação de safety flags, como `sends_orders=true`, `changes_risk=true` ou `exchange_private_access=true`.

## Validação

```powershell
python -m compileall -q smartcrypto scripts tests
python -m pytest -q tests/test_critical_notifications_dashboard_panel.py
python -m pytest -q tests/test_notification_channels.py
python -m pytest -q tests/test_current_project_handover_audit.py
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```
