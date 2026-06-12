# Notification Root Bootstrap Permission Audit v1

## Decisão

O override `user: "0:0"` do serviço `trade-event-notifications-paper` foi
mantido temporariamente. Removê-lo nesta branch não é operacionalmente seguro.

O serviço monta `./data:/app/data` para compartilhar o estado e o relatório com o
host e com os demais componentes paper. Em Docker Desktop/Windows e em migrações
de VPS, o ownership desse bind mount não é garantido para o UID `10001`. Já houve
falha operacional comprovada ao gravar
`data/reports/trade_event_notifications_report.json` com `PermissionError`.

Esta é uma exceção de bootstrap, não uma autorização para o daemon operar como
root.

## Escopo mínimo de root

O fluxo do container é:

1. Compose inicia `scripts/docker_runtime_permissions_bootstrap.py` como root.
2. O bootstrap aceita somente `/app/data/reports` e `/app/data/runtime`.
3. Os dois caminhos recebem UID/GID `10001`, diretórios `700` e arquivos `600`.
4. UID ou GID zero são rejeitados pela CLI do bootstrap.
5. O processo executa `setgid(10001)` e `setuid(10001)`.
6. Somente depois, `execvp` inicia `scripts/run_trade_event_notifications.py`.

O banco Freqtrade permanece montado read-only em `/paper-db`. Nenhum caminho de
trading, risco, modelo, dataset ou sinais ativos é alterado.

## Auditoria read-only

Executar:

```powershell
python scripts/audit_notification_runtime_permissions.py --project-root . --json
```

O auditor lê estaticamente `docker-compose.paper.yml` e o AST do bootstrap. Ele
não usa Docker, não importa o dispatcher de notificações, não faz HTTP e não lê
secrets. O resultado esperado no contrato atual é:

```text
status=ok
reason=temporary_root_bootstrap_justified_and_privileges_dropped
runs_as_root=true
root_required=true
privilege_drop_verified=true
permission_paths_limited=true
```

A escrita do relatório é opcional e somente ocorre com `--write`:

```powershell
python scripts/audit_notification_runtime_permissions.py --project-root . --write --json
```

O relatório local fica em
`data/reports/notification_runtime_permissions_audit.json` e não deve ser
versionado.

## Condições de bloqueio

O auditor retorna `blocked` se:

- o Compose ou o serviço estiver ausente;
- root for usado sem bind mount e bootstrap justificáveis;
- o bootstrap não limitar os caminhos;
- UID/GID root puderem ser mantidos;
- `setgid`, `setuid` ou `execvp` não forem comprovados;
- o daemon não estiver depois do separador do bootstrap.

## Remoção futura do override

A recomendação é migrar os arquivos graváveis da notificação para volumes Linux
pré-provisionados com UID/GID `10001`, mantendo apenas snapshots/exportações para
o host. Depois dessa migração e de uma validação operacional de escrita, o
`user: "0:0"` poderá ser removido. Essa mudança de armazenamento fica fora desta
branch para não ampliar o risco sobre o runtime paper.

## Segurança

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`

Os testes e o auditor não enviam Telegram/NTFY real, não acessam exchange e não
dependem de Docker.
