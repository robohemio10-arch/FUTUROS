# Docker Paper Runtime Permissions And Runtime Lock V1

## Objetivo

Corrigir falhas de permissao em bind mounts do runtime paper sem ampliar a
autoridade dos servicos e atualizar o pin vulneravel do GitPython no lock da
imagem SmartCrypto. A mudanca permanece paper/shadow only.

## Root Cause

Os bind mounts de `data/` continham diretorios e arquivos criados por processos
com ownership diferente do usuario non-root da imagem (`10001:10001`). Como
resultado:

- Phase14 falhava ao escrever
  `data/reports/freqtrade_paper_db_snapshot_export.json` e
  `data/trades/freqtrade_paper_trades_raw.parquet`;
- o scheduler de autolearning falhava ao escrever
  `data/reports/paper_autolearning_foundation_summary.json`;
- o servico de notificacoes ja precisava do mesmo bootstrap limitado para seu
  report e estado idempotente.

O lock runtime tambem continha `GitPython==3.1.50`, versao bloqueada pelo audit
de dependencias.

## Allowlist Fechada

O bootstrap aceita exclusivamente:

| Servico | Diretorios autorizados |
| --- | --- |
| `phase14-feedback-sync-paper` | `/app/data/reports`, `/app/data/trades`, `/app/data/snapshots/freqtrade-paper` |
| `paper-autolearning-scheduler` | `/app/data/reports`, `/app/data/feedback` |
| `trade-event-notifications-paper` | `/app/data/reports`, `/app/data/runtime` |

`/app/data` nunca e autorizado. Paths relativos, barras invertidas, traversal,
duplicatas, paths de outro perfil e symlinks no target ou em componentes
existentes sao bloqueados.

O bootstrap cria somente o target autorizado quando seu parent ja existe. Ele
percorre apenas esse target, com `followlinks=False`, aplica ownership
`10001:10001`, modo `0700` em diretorios e `0600` em arquivos regulares
existentes. Nenhum `chmod 777` ou `chown` sobre `/app/data` e permitido.

## Root Efemero E Drop De Privilegios

Os tres servicos usam `user: "0:0"` apenas para o bootstrap do bind mount. O
bootstrap executa, nesta ordem:

1. validacao nominal do perfil e paths;
2. preparacao de ownership e modos;
3. `setgroups([])`;
4. `setgid(10001)`;
5. `setuid(10001)`;
6. verificacao de UID e GID efetivos;
7. `os.execvp` com o argv original da aplicacao.

Falha de path, metadata, ownership, chmod, drop ou exec retorna bloqueio
controlado. Logs contem somente evento, servico, contagens e safety flags; argv,
tokens, ambiente e conteudo de arquivos nao sao impressos.

## Safety Flags

O Compose preserva em todos os servicos afetados:

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false
```

O volume `/paper-db` continua read-only e o dashboard continua com
`./data:/app/data:ro`. A mudanca nao altera Freqtrade, RiskManager, strategy,
stake, leverage, stoploss, ROI, modelos, registries, Qlib ativo, IA Shadow
ativa, Decision Ledger ou `.env`.

## GitPython Runtime

`requirements-runtime.lock` foi regenerado pelo fluxo documentado do
resolvedor `pip` em venv temporario:

1. pins runtime existentes foram usados como input fechado;
2. o pin canonico `GitPython==3.1.51`, ja declarado em `pyproject.toml`, foi
   aplicado ao input;
3. o ambiente foi resolvido e instalado pelo `pip`;
4. `pip freeze -r` produziu o conjunto resolvido;
5. o renderer deterministico preservou ordem e grafia do lock.

A unica alteracao resolvida foi GitPython `3.1.50 -> 3.1.51`. Nao houve mudanca
transitiva. `requirements-dev.lock` e `requirements-qlib.lock` permanecem
inalterados.

## Smoke Test Controlado

Validar o Compose sem iniciar a stack completa:

```powershell
docker compose -f docker-compose.paper.yml --profile autolearning --profile notifications config --quiet
docker compose -f docker-compose.paper.yml build phase14-feedback-sync-paper paper-autolearning-scheduler
docker compose -f docker-compose.paper.yml run --rm phase14-feedback-sync-paper python scripts/run_phase14_runtime_feedback_sync.py --source-db /paper-db/tradesv3.paper.sqlite --once
docker compose -f docker-compose.paper.yml --profile autolearning run --rm paper-autolearning-scheduler
```

O servico `trade-event-notifications-paper` nao deve ser iniciado durante esta
validacao. Nao usar `--send-real`, `docker compose up`, `down -v` ou qualquer
comando que altere volumes nomeados.

## Rollback

1. parar somente os containers efemeros criados por `docker compose run --rm`,
   caso ainda existam;
2. restaurar os arquivos versionados desta branch por processo Git controlado;
3. reconstruir apenas as imagens afetadas;
4. repetir `docker compose config --quiet` e os testes estaticos;
5. nao rebaixar GitPython para `3.1.50`.

O rollback nao deve executar `down -v`, alterar o banco paper, aplicar chmod
amplo, iniciar notificacoes ou habilitar live/orders/private exchange.
