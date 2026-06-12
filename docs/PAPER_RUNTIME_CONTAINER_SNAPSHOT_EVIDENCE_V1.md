# Paper Runtime Container Snapshot Evidence v1

## Objetivo

Esta evidência confirma, de forma opcional e read-only, se os serviços do runtime
paper estão realmente em execução. Ela complementa a frescura dos relatórios: um
JSON recente não é usado como prova de que o processo produtor continua vivo.

## Coleta segura

A coleta somente ocorre com `--collect-containers` e executa:

```text
docker compose -f docker-compose.paper.yml ps --format json
```

O comando apenas consulta o projeto Compose. Ele não inicia, reinicia, interrompe
ou altera containers. Se Docker estiver ausente, indisponível ou retornar conteúdo
inválido, o auditor produz diagnóstico `unknown` ou `degraded`, sem traceback fatal.

Sem a flag, o contrato permanece conservador:

```text
container_snapshot_status=disabled
docker_services_status=disabled
freqtrade_paper_status=unknown
smartcrypto_bot_status=unknown
paper_runtime_alive=false
reason=container_collection_not_requested
```

## Serviços

Serviços críticos para `paper_runtime_alive=true`:

- `freqtrade-paper`
- `phase14-feedback-sync-paper`
- `qlib-refresh-supervisor-paper`
- `smartcrypto-bot-paper`
- `smartcrypto-dashboard-paper`

O serviço `trade-event-notifications-paper` é opcional. Sua ausência ou falha
degrada a evidência, mas não redefine a lista crítica e nunca libera readiness.

Estados `running`, `Up` ou `healthy` são classificados como `ok`. Containers
ausentes, `exited`, `restarting`, `dead` ou `unhealthy` são registrados por serviço.
Qualquer falha crítica mantém `paper_runtime_alive=false` e bloqueia a saúde do
runtime paper.

## Uso

Auditoria sem materializar relatório:

```powershell
python scripts/audit_paper_runtime_health_and_freshness.py --project-root . --collect-containers --json
```

Auditoria com relatório runtime local:

```powershell
python scripts/audit_paper_runtime_health_and_freshness.py --project-root . --collect-containers --write --json
```

Propagação ao evidence pack e readiness snapshot:

```powershell
python scripts/build_runtime_evidence_pack_and_readiness_snapshot_v2.py --project-root . --collect-containers --json
```

Sem a flag, ambos os CLIs preservam o modo desabilitado. A opção antiga
`--include-containers` permanece apenas como alias de compatibilidade oculto.

## Runtime Evidence e Dashboard

O runtime evidence reutiliza o snapshot produzido pelo auditor; não executa uma
segunda coleta Docker. Os snapshots de Infrastructure e Active Controls exibem os
status por serviço como evidência read-only. Mesmo quando todos os containers
críticos estão `ok`, readiness, canary e live permanecem bloqueados pelos gates
institucionais independentes, incluindo soak, gaps e Monte Carlo.

## Garantias de segurança

- `paper_only=true` e `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`

A implementação não usa exchange privada, não envia ordens, não altera risco,
modelo, datasets, OCR, sinais ativos ou `docker-compose.paper.yml`.
