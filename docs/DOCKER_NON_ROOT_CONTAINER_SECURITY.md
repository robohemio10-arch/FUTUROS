# Docker non-root container security

Esta frente endurece os containers SmartCrypto sem alterar trading, risco,
modelos, sinais ou runtime operacional. O contrato segue paper/shadow only.

## Usuario nao-root

Os Dockerfiles `docker/smartcrypto/Dockerfile`,
`docker/dashboard/Dockerfile` e `docker/qlib/Dockerfile` criam o usuario
`smartcrypto` com UID/GID 10001 e terminam com:

```dockerfile
USER smartcrypto
```

O `HEALTHCHECK` continua presente e roda sob esse usuario nao-root. Isso reduz
o impacto de falhas dentro do container e evita dependencia institucional de
root para executar scripts Python do projeto.

## Permissoes de diretorios

Antes da troca para `USER smartcrypto`, cada imagem cria e ajusta permissao dos
diretorios de runtime necessarios:

- `/app/data`
- `/app/data/reports`
- `/app/data/runtime`
- `/app/data/features`, quando aplicavel
- `/app/data/predictions`, quando aplicavel
- `/app/logs`

No host, os bind mounts continuam os mesmos. Em ambientes Linux, se um volume
host vier com permissao restritiva, ajuste a permissao do diretorio host para o
UID/GID 10001 em vez de voltar o container para root.

## Healthcheck

O healthcheck oficial permanece:

```bash
python -m smartcrypto.runtime.container_healthcheck --quiet
```

Ele valida o modo paper, bloqueia flags perigosas e nao envia ordens, nao chama
endpoint privado e nao toca no banco operacional do Freqtrade.

## Paper/shadow only

Os composes mantem:

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false
```

O arquivo `docker-compose.live.example.yml` continua sendo exemplo seguro:
servicos sem restart automatico, flags de live/order bloqueadas e config
Freqtrade com `dry_run=true`. Esta mudanca nao libera live trading.

## Validacao local

Use:

```bash
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_docker_non_root_container_security.py -q
python -m pytest tests/test_docker_healthcheck_backup_restore_runbooks.py -q
python -m pytest -q
```

Opcionalmente, valide a imagem:

```bash
docker compose -f docker-compose.paper.yml build smartcrypto-bot-paper smartcrypto-dashboard-paper qlib-worker-paper
docker compose -f docker-compose.paper.yml run --rm smartcrypto-bot-paper id
```

O retorno esperado do `id` deve apontar para o usuario `smartcrypto`, nao root.
