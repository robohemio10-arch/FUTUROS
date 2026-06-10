# NTFY / Telegram Dashboard Test Tab

Objetivo:
Adicionar uma aba especifica no dashboard Streamlit para validar e testar NTFY e Telegram no runtime paper/shadow do FUTUROS.

Arquivos:
- smartcrypto/dashboard/notification_channels_test_panel.py
- smartcrypto/dashboard/app.py
- tests/test_notification_channels_dashboard_test_panel.py
- docs/NTFY_TELEGRAM_DASHBOARD_TEST_TAB.md

Funcionalidades:
1. Mostra configuracao sanitizada dos canais.
2. Mostra enabled, configured e validation_error.
3. Mostra ultimo dispatch manual sanitizado.
4. Executa dry-run.
5. Permite envio real manual apenas com confirmacao textual.
6. Preserva flags de seguranca.

Confirmacao exigida para envio real:
ENVIAR TESTE

Relatorio runtime:
data/reports/manual_notification_test_dispatch_report.json

O relatorio runtime nao deve ser versionado.

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

Dados nunca exibidos:
- Telegram bot token
- Telegram chat id completo
- NTFY topic completo
- NTFY token
- Authorization headers
- URLs Telegram contendo token

Validacao:
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_notification_channels_dashboard_test_panel.py -q
python -m pytest tests/test_critical_notifications_dashboard_panel.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json

Limite:
Esta aba e observabilidade/teste de comunicacao. Nao libera live, canary ou readiness.
