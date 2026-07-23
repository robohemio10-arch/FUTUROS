# Core Paper Prediction Path and Startup Ordering Hardening V1

## Objetivo

Este contrato elimina a divergencia entre o parquet produzido pelo supervisor
Qlib e o parquet consumido pelo bot paper. Ele tambem remove a corrida de cold
start e impede que um relatorio residual de uma instancia anterior libere o bot.

O escopo permanece paper/shadow only. Nenhuma alteracao concede autoridade para
live, canary, ordens, exchange privada, risco, treino ou promocao de modelo.

## Incidente

O bot consumia:

```text
/app/data/predictions/latest_predictions.parquet
```

O supervisor Qlib produzia:

```text
/app/data/predictions/latest_qlib_predictions.parquet
```

Os dois servicos iniciavam simultaneamente. Durante o cold start, o supervisor
ainda preparava ownership e modos de `/app/data/predictions` quando o bot tentava
ler o artefato legado. O resultado observado foi `PermissionError`.

## Caminho Canonico

O unico caminho operacional do core paper passa a ser:

```text
/app/data/predictions/latest_qlib_predictions.parquet
```

O mesmo nome e usado pelo ambiente Compose e pelo default de
`RuntimeSettings.from_env()`. Nao existe alias, copia, symlink ou fallback para o
nome legado.

## Readiness Qlib

O modulo `smartcrypto.runtime.qlib_refresh_supervisor_healthcheck` valida, sem
escrita:

- relatorio JSON regular, valido e nao residual;
- `status=ok`;
- market features e predictions com `status=ok`;
- Phase 13 com `ok` ou `empty`;
- input data fresh;
- runtime paper/shadow e flags de seguranca bloqueadas;
- predictions e market features regulares, nao vazios e sem symlink.

Qualquer ausencia, schema invalido, flag insegura, artefato vazio ou erro de
`/proc` retorna readiness bloqueado.

## Gate Da Instancia Atual

Idade relativa isolada nao prova que um relatorio pertence ao container atual.
O healthcheck calcula o inicio do PID 1 usando:

1. `btime` de `/proc/stat`;
2. `starttime` de `/proc/1/stat`;
3. `SC_CLK_TCK` do kernel.

O `generated_at` do relatorio deve ser posterior ao inicio do PID 1, com apenas
cinco segundos de tolerancia para granularidade do kernel. Timestamp futuro alem
da tolerancia, relatorio stale ou `/proc` inconsistente bloqueiam o healthcheck.

O limite de idade e `420` segundos. Ele cobre o intervalo nominal de `300`
segundos do supervisor com margem operacional, sem aceitar indefinidamente uma
evidencia antiga.

## Sequencia De Cold Start

```text
qlib-refresh-supervisor-paper
  -> bootstrap root nominal
  -> ownership e modos minimos
  -> setgid/setuid 10001
  -> probe non-root de gravabilidade
  -> primeiro ciclo Qlib
  -> report da instancia atual
  -> health=healthy

smartcrypto-bot-paper
  -> aguarda condition: service_healthy
  -> inicia como usuario non-root da imagem
  -> le latest_qlib_predictions.parquet
```

O bot nao recebe `user: 0:0` e nao possui bootstrap concorrente sobre
predictions.

## Limites Operacionais

Esta branch nao:

- inicia ou reinicia containers;
- executa notificacoes ou autolearning;
- ativa Decision Ledger;
- acessa exchange privada;
- envia ordens;
- altera RiskManager, SignalExporter, estrategia ou parametros financeiros;
- carrega ou treina modelos no healthcheck;
- escreve relatórios ou artefatos pelo healthcheck.

## Certificacao Runtime Posterior

Depois de merge e em janela controlada, o cold start deve confirmar:

1. supervisor healthy e `RestartCount=0`;
2. PID 1 do supervisor em `10001:10001` apos o bootstrap;
3. primeiro ciclo com relatorio `status=ok` da instancia corrente;
4. bot iniciado somente depois do healthcheck;
5. ausencia de `PermissionError` e traceback;
6. nenhum profile de notificacoes ou autolearning iniciado.

Essa certificacao nao e executada nesta branch.

## Validacao Estatica

```powershell
python -m compileall -q scripts smartcrypto tests
python -m pytest tests/test_qlib_refresh_supervisor_healthcheck_v1.py tests/test_core_paper_prediction_path_and_startup_ordering_hardening_v1.py -q
python -m ruff check smartcrypto/runtime/qlib_refresh_supervisor_healthcheck.py smartcrypto/settings.py tests/test_qlib_refresh_supervisor_healthcheck_v1.py tests/test_core_paper_prediction_path_and_startup_ordering_hardening_v1.py
python -m mypy smartcrypto/runtime/qlib_refresh_supervisor_healthcheck.py smartcrypto/settings.py --ignore-missing-imports
docker compose -f docker-compose.paper.yml --profile qlib --profile optional --profile autolearning --profile notifications config --quiet
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r requirements-runtime.lock --progress-spinner off
python -m pip_audit -r requirements-qlib.lock --progress-spinner off
```
