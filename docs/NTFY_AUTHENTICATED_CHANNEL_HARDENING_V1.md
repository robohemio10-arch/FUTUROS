# NTFY Authenticated Channel Hardening V1

## Objetivo

Este contrato impede que o canal NTFY seja usado sem autenticacao ou com uma
configuracao ambigua. A validacao ocorre antes da leitura de trades, abertura do
banco de idempotencia, chamada HTTP, escrita de relatorio ou entrada no loop do
daemon.

O componente permanece exclusivamente `paper/shadow`. Ele nao envia ordens,
nao acessa exchange privada e nao altera risco, sinais ou modelos.

## Contencao operacional

A contencao aplicada pelo operador mantem NTFY desabilitado e seleciona
explicitamente Telegram. Ela existe fora do repositorio e nao e lida, copiada
ou alterada por esta branch. O hardening versionado complementa essa contencao
com uma barreira fail-closed.

## Threat Model

Os riscos tratados sao:

- publicacao acidental em topico NTFY sem autenticacao;
- credencial Basic incompleta ou combinada com Bearer;
- transporte HTTP sem TLS ou credencial embutida na URL;
- fallback silencioso de `all` para apenas Telegram;
- processamento de trades e escrita de estado antes da descoberta de erro;
- exposicao de credenciais ou corpos HTTP em resultados estruturados.

## Contrato De Autenticacao

NTFY aceita exatamente um dos modos:

- `bearer`: valor nao vazio fornecido pela configuracao segura do ambiente;
- `basic`: usuario e senha fornecidos em conjunto.

Ausencia de autenticacao, Basic parcial e combinacao entre Bearer e Basic sao
bloqueadas. A URL deve ser HTTPS, possuir host valido e nao pode conter
userinfo. O timeout deve ser numerico e maior que zero.

O resultado informa apenas `auth_mode` como `none`, `bearer`, `basic` ou
`ambiguous`. Nenhuma caracteristica da credencial e materializada.

## Matriz De Canais

| Modo | Telegram | NTFY | Resultado |
| --- | --- | --- | --- |
| `telegram` | habilitado e valido | ignorado | permitido |
| `telegram` | ausente ou invalido | ignorado | bloqueado |
| `ntfy` | ignorado | habilitado, HTTPS e autenticado | permitido |
| `ntfy` | ignorado | ausente, inseguro ou invalido | bloqueado |
| `all` | habilitado e valido | habilitado, HTTPS e autenticado | permitido |
| `all` | valido | desabilitado ou invalido | bloqueado, sem fallback |

## Razoes De Bloqueio

As principais razoes estaveis sao:

- `telegram_disabled`;
- `missing_telegram_bot_token`;
- `missing_telegram_chat_id`;
- `ntfy_disabled`;
- `missing_ntfy_topic`;
- `invalid_ntfy_server_url`;
- `ntfy_https_required`;
- `ntfy_url_userinfo_not_allowed`;
- `missing_ntfy_authentication`;
- `invalid_ntfy_basic_auth_pair`;
- `ambiguous_ntfy_auth`;
- `invalid_timeout_seconds`.

## Seguranca De Logs

O preflight retorna somente status, razao, canais selecionados, checks falhos,
flags de habilitacao, modo de autenticacao e flags de seguranca. Ele nunca
retorna topico, chat ID, usuario, senha, credencial, header de autorizacao, URL
com userinfo ou corpo de resposta HTTP.

## Habilitacao Futura

Para habilitar NTFY no futuro, o operador deve provisionar um canal privado,
HTTPS e um unico modo de autenticacao no mecanismo local de secrets. Em seguida,
deve executar o CLI em `--dry-run` com `--channels ntfy` e somente depois testar
`--channels all`. Nenhum valor sensivel deve ser passado pela linha de comando.

## Rollback

O rollback institucional e selecionar explicitamente `telegram` e manter NTFY
desabilitado. `all` nunca representa fallback e deve permanecer bloqueado se um
dos canais obrigatorios estiver indisponivel.

## Fora De Escopo

Esta entrega nao altera `.env`, Docker, Freqtrade, RiskManager, Qlib, IA Shadow,
modelos, registry, datasets, sinais, containers ou artefatos em `data/`.

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests -q -k "notification or ntfy or telegram"
python -m ruff check scripts/run_trade_event_notifications.py smartcrypto/ops/notification_channels.py smartcrypto/ops/trade_event_notifications.py tests
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r requirements-dev.lock
git diff --check
git status --short -- data
```
