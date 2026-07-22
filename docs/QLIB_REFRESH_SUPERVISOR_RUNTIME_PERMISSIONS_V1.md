# Qlib Refresh Supervisor Runtime Permissions V1

## Objetivo

Este contrato corrige as permissoes dos bind mounts usados pelo servico
`qlib-refresh-supervisor-paper`. O servico continua paper/shadow only e nao
recebe autoridade sobre trading, risco, ordens, modelos, Decision Ledger ou
exchange privada.

O processo inicia como root somente no bootstrap de permissoes. O bootstrap
prepara as superficies nominais, executa `setgid(10001)` antes de
`setuid(10001)`, valida a identidade efetiva e substitui o proprio processo por
`os.execvp`. O supervisor final, inclusive o PID 1, executa como `10001:10001`.

## Causa Raiz

O bind mount `./data:/app/data` preservava ownership do host. O diretorio
`data/runtime` estava em modo `0700` com UID/GID `1000:1000`, enquanto o
supervisor executava como `10001:10001`. A leitura de
`data/runtime/active_freqtrade_signals.json` falhava com `PermissionError` e o
container reiniciava.

O erro nao era ausencia de arquivo e nao deve ser mascarado como tal.

## Perfil Nominal

O perfil `qlib-refresh-supervisor-paper` autoriza somente estes diretorios:

- `/app/data/runtime`
- `/app/data/reports`
- `/app/data/features`
- `/app/data/predictions`

Os arquivos abaixo ficam explicitamente documentados como cobertos pelos
diretorios autorizados:

- `/app/data/runtime/active_freqtrade_signals.json`
- `/app/data/reports/qlib_market_features_refresh_report.json`
- `/app/data/reports/qlib_market_features_refresh_report.json.tmp`

O bootstrap nao cria nem recria esses arquivos. Arquivos regulares existentes
sao preservados byte a byte. Diretorios recebem owner `10001:10001` e modo
`0700`; arquivos regulares existentes recebem owner `10001:10001` e modo
`0600`.

Nao existe autorizacao para `/app/data`, wildcard, path relativo, traversal ou
symlink. Tipos diferentes de diretorio e arquivo regular bloqueiam o bootstrap.

## Compatibilidade

Os perfis existentes permanecem fechados:

- Phase14: reports, trades e snapshot paper;
- autolearning: reports e feedback;
- notificacoes: reports e runtime.

O argv original do supervisor permanece:

```text
python scripts/run_qlib_paper_refresh_supervisor.py --interval-seconds 300
```

Ele e passado ao bootstrap depois de `--`, sem shell e sem concatenacao de
comando.

## Safety Flags

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false
sends_orders=false
changes_risk=false
```

O Decision Ledger permanece `enabled=false`, `writer_enabled=false` e
`trade_link_enabled=false`.

## Validacao Estatica

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_qlib_refresh_supervisor_runtime_permissions_v1.py -q
python -m pytest tests/test_docker_runtime_permissions_bootstrap.py tests/test_docker_paper_runtime_permissions_and_runtime_lock_v1.py -q
python -m pytest tests/test_qlib_paper_refresh_supervisor.py -q
python -m ruff check scripts/docker_runtime_permissions_bootstrap.py tests/test_qlib_refresh_supervisor_runtime_permissions_v1.py tests/test_docker_runtime_permissions_bootstrap.py tests/test_docker_paper_runtime_permissions_and_runtime_lock_v1.py tests/test_qlib_paper_refresh_supervisor.py
python -m mypy scripts/docker_runtime_permissions_bootstrap.py --ignore-missing-imports
docker compose -f docker-compose.paper.yml config --quiet
python -m pip_audit -r requirements-runtime.lock --progress-spinner off
```

## Smoke Isolado

Somente o supervisor Qlib pode ser construido e iniciado neste gate:

```powershell
docker compose -f docker-compose.paper.yml build qlib-refresh-supervisor-paper
docker compose -f docker-compose.paper.yml up -d qlib-refresh-supervisor-paper
```

Validar health, restart count, identidade do PID 1, acessibilidade das quatro
superficies, GitPython `3.1.51` e ausencia de `PermissionError` ou traceback.
Parar apenas o servico ao final, sem `down -v`, prune ou remocao de volumes.

## Fora De Escopo

Esta alteracao nao modifica o supervisor Python, produtor de sinais,
Freqtrade, RiskManager, OrderManager, Qlib, IA Shadow, modelos, parametros
financeiros, sinais ativos, notificacoes ou arquivos versionados em `data/`.
