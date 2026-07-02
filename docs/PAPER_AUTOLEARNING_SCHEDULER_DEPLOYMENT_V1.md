# Paper Auto-learning Scheduler Deployment V1

## Objetivo

Esta branch transforma o scheduler lógico de autoaprendizado paper em um contrato
de deployment auditável para o ambiente Docker paper. O mecanismo escolhido é
Docker Compose paper, porque o runtime paper do projeto já usa
`docker-compose.paper.yml`.

O deployment é intencionalmente controlado: o código desta branch não inicia
container, não cria cron, não cria timer systemd e não cria tarefa Windows. Ele
define o serviço paper-only e entrega um auditor estático para validar se o
serviço, o comando e o contrato de kill-switch estão prontos.

## Mecanismo escolhido

Mecanismo único:

```text
docker_compose_paper
```

Serviço definido:

```text
paper-autolearning-scheduler
```

O serviço fica em profile explícito `autolearning` e usa `restart: "no"`.
Isso evita execução acidental em `docker compose up` sem profile e impede loop
por restart automático. A ativação operacional deve ser controlada pelo operador
paper.

## Comando validado

O auditor exige exatamente:

```text
python scripts/run_paper_autolearning_scheduler_v1.py --project-root /app --once --write-feedback --train-smoke --json
```

Esse comando executa o scheduler paper uma vez, com feedback e smoke tests
paper/shadow. Ele não atualiza master, não promove modelo e não altera active
model.

## Kill-switch

Contrato versionado:

```text
docker/paper-autolearning-scheduler/autolearning_scheduler_kill_switch.template.json
```

Caminho runtime planejado:

```text
data/runtime/autolearning_scheduler_kill_switch.json
```

O arquivo em `data/runtime` é runtime artifact e não deve ser versionado. O
auditor usa o template versionado para confirmar que existe contrato de
kill-switch antes de considerar o deployment pronto.

## Auditor

Executar:

```powershell
python .\scripts\audit_paper_autolearning_scheduler_deployment_v1.py --project-root . --json
```

Resultado esperado quando o compose e o contrato estão íntegros:

```text
status=ok
deployment_status=deployment_ready
foundation_runner_command_validated=true
kill_switch_required=true
kill_switch_contract_present=true
kill_switch_checked=true
deployment_performed=false
```

Se o contrato de kill-switch estiver ausente, o status correto é:

```text
status=blocked
reason=kill_switch_contract_missing
deployment_status=blocked
```

## Garantias de segurança

O deployment preserva:

- `paper_only=true`
- `shadow_only=true`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false` no auditor
- `writes_sqlite=false`
- `master_update_performed=false`
- `model_promotion_performed=false`
- `active_model_changed=false`

## Fora de escopo

Esta branch não:

- cria cron, timer systemd ou tarefa Windows;
- inicia Docker;
- altera Freqtrade;
- altera RiskManager;
- altera Qlib runtime;
- altera IA Shadow runtime;
- altera modelos;
- altera `trades_master`;
- altera configs live;
- promove modelo;
- envia ordens;
- acessa exchange privada.

## Validação

Comandos principais:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests\test_paper_autolearning_scheduler_deployment_v1.py -q
python -m pytest tests\test_paper_autolearning_daily_scheduler_v1.py tests\test_paper_autolearning_foundation_v1.py -q
python .\scripts\audit_paper_autolearning_scheduler_deployment_v1.py --project-root . --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
git status --short
```
