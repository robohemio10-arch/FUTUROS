# Core Paper Bootstrap Serialization and Phase14 Atomic Snapshot V1

## Objetivo

Este contrato elimina a concorrencia estrutural observada no cold start controlado V4
sem habilitar live, ordens, exchange privada, autolearning, notificacoes reais ou
Decision Ledger. A mudanca permanece restrita ao runtime paper/shadow e nao altera
estrategia, risco, stake, leverage, ROI, stoploss, modelos ou sinais.

## Incidente V4 e contencao

O cold start V4 foi contido depois que o supervisor Qlib e o Phase 14 completaram
bootstrap root, queda para UID/GID `10001` e probe de escrita sem retry. Os writers
operacionais falharam logo depois:

- Qlib: `PermissionError` ao criar o tempfile exclusivo de
  `market_features_60d.parquet`;
- Phase 14: `PermissionError` no tempfile deterministico do snapshot SQLite;
- Freqtrade: startup simultaneo com tentativa de ajuste recursivo de ownership em
  `/freqtrade/user_data`, que incluia o bind amplo `./data`.

Os logs demonstram concorrencia sobre metadados do mesmo bind mount. Eles nao provam
que o Freqtrade foi a causa exclusiva. A remediacao, portanto, remove a superficie
compartilhada e serializa os dois bootstraps que ainda possuem autoridade root
temporaria.

## Isolamento do Freqtrade

O Freqtrade paper nao monta mais `./data:/freqtrade/user_data/data`. Seu diretorio
interno usa o named volume dedicado `futuros_freqtrade_paper_data`. O named volume
historico do banco permanece `futuros_freqtrade_paper_db`, sem mudanca de nome,
DB URL ou contrato.

A estrategia recebe somente `./data/runtime` em
`/freqtrade/user_data/data/runtime:ro`. Esse mount cobre o sinal ativo e o controle
de saida paper existentes. Features, predictions, reports, snapshots, trades e
feedback nao ficam expostos ao startup do Freqtrade.

## Ordem e readiness

A ordem declarativa e:

```text
Freqtrade paper healthy
-> Qlib bootstrap, primeiro ciclo e current-instance health
-> Phase 14 bootstrap, primeiro sync e current-instance health
-> bot liberado pelo health do Qlib
```

O healthcheck Freqtrade usa somente stdlib e valida PID 1, comando do worker paper,
uptime minimo, config com `dry_run=true` e banco SQLite aberto por URI `mode=ro`
com a tabela `trades`.

O healthcheck Phase 14 valida report atual da mesma instancia, contrato paper/shadow,
Decision Ledger desabilitado e snapshot regular, nao symlink, nao vazio, legivel e
com schema `trades`. Tempfile deterministico ou residuo exclusivo bloqueia readiness.
Ambos retornam exit code `1` para qualquer inconsistencia.

## Lock do bootstrap

Qlib e Phase 14 escrevem em subarvores diferentes, mas ambos preparam
`/app/data/reports`. O Compose nao impede que outro profile bootstrap seja iniciado
em paralelo. Por isso foi adicionado um lock advisory POSIX real em:

```text
/app/data/reports/.runtime-permissions-bootstrap.lock
```

O lock:

- usa `fcntl.flock` exclusivo e non-blocking com timeout delimitado;
- rejeita symlink e arquivo nao regular;
- e adquirido antes da travessia e do `chown`;
- permanece durante prepare, queda de privilegio e probes;
- e liberado antes do `exec` da aplicacao;
- nao e removido e nao usa PID file ou limpeza oportunista;
- falha fechado quando `fcntl`, `fchmod`, open, acquire ou release falham;
- possui factory injetavel para testes Windows sem transformar o lock em no-op
  operacional.

O lock serializa apenas o bootstrap. Nao serializa os daemons depois do `exec` e nao
substitui o isolamento dos destinos de escrita.

## Snapshot SQLite Phase 14

O exporter preserva suas quatro APIs publicas. Cada invocation cria um tempfile
exclusivo no mesmo diretorio do target, abre a fonte por SQLite URI `mode=ro`, fecha
as conexoes, executa `fsync` e promove com `os.replace`.

Criacao e promocao possuem retries curtos e limitados apenas para erros transitorios.
Cada invocation limpa somente o tempfile que recebeu de `mkstemp`. Falhas de backup,
promocao ou cleanup retornam report `blocked`, com erro sanitizado, e preservam o
snapshot anterior. O comando inline do export por named volume aplica o mesmo
contrato; nenhum `.snapshot.sqlite.tmp` deterministico e criado ou removido.

## Limites operacionais

Esta branch nao executa containers e nao constitui certificacao runtime. O cold start
V5 permanece pendente e deve ser conduzido por procedimento operacional separado,
depois de merge e revisao.

## Errata operacional V5

O cold-start V5 encontrou um defeito de normalizacao no exporter do snapshot
Phase 14: o parent absoluto retornado por `tempfile.mkstemp` era comparado com o
parent logico relativo do target. Um tempfile corretamente confinado era, assim,
classificado como externo e o snapshot retornava
`snapshot_tempfile_creation_failed`.

A serializacao do bootstrap, a queda para UID/GID 10001 e a writability foram
comprovadas. O incidente nao foi um `PermissionError` operacional. A certificacao
final continua pendente ate a correcao ser integrada e o cold-start V5.2 ser
executado por procedimento separado.

As invariantes permanecem:

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

Autolearning e notifications continuam isolados por profiles. O Decision Ledger
continua com `enabled=false`, `writer_enabled=false`, `trade_link_enabled=false` e
sem autoridade de escrita runtime.

## Validacao sem runtime

```powershell
python -m compileall -q scripts smartcrypto tests
python -m ruff check <arquivos Python alterados>
python -m mypy <modulos de producao alterados> --ignore-missing-imports
python -m pytest <testes novos e regressoes relacionadas> -q
docker compose -f docker-compose.paper.yml --profile qlib --profile optional --profile autolearning --profile notifications config --quiet
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r requirements-runtime.lock --progress-spinner off
python -m pip_audit -r requirements-qlib.lock --progress-spinner off
git diff --check
```
