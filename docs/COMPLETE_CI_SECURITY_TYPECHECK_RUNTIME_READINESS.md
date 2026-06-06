# Complete CI security typecheck runtime readiness

Esta frente completa o baseline institucional de CI, security, typecheck,
manifesto e readiness de runtime. Ela nao altera trading, risco, IA, modelos,
datasets, Freqtrade DB, stake, leverage ou live readiness.

## Pipeline CI

O workflow `.github/workflows/ci.yml` executa:

- instalacao reproduzivel com `requirements-dev.lock`;
- instalacao editavel do pacote com `python -m pip install -e .`;
- validacao de flags paper/shadow only;
- `make compile`;
- `make lint`;
- `make typecheck`;
- `make security`;
- checagem deterministica do `PROJECT_MANIFEST_CLEAN.json`;
- `make test`;
- build dos Dockerfiles SmartCrypto, Dashboard e Qlib;
- smoke test de `smartcrypto.runtime.container_healthcheck`.

O CI nao usa GitHub secrets, nao acessa exchange privada, nao envia ordens, nao
sobe container live e nao altera artefatos runtime.

## Makefile

Os alvos institucionais sao reais:

- `make lint`: executa `ruff check` no escopo inicial configurado.
- `make typecheck`: executa `mypy` no escopo inicial configurado.
- `make security`: executa teste local, Bandit, pip-audit e secret scan.
- `make audit`: encadeia compile, lint, typecheck, security e testes rapidos.
- `make paper-check`: executa o container healthcheck em modo paper.

O escopo de lint/typecheck e incremental de proposito. Ele cria um gate real
sem transformar esta branch em uma refatoracao global da base historica.

## Security scan

`bandit` roda com excecoes rastreadas em
`docs/security_audit_exceptions.md`. `pip-audit` roda contra o lock direto com
`--disable-pip --no-deps`, evitando resolver dependencias transitivas fora do
arquivo institucional. O secret scan local le somente arquivos versionados por
`git ls-files` e ignora runtime/data/logs.

## Manifesto

`scripts/generate_project_manifest.py` gera `PROJECT_MANIFEST_CLEAN.json` de
forma deterministica. O manifesto registra contagens reais, hashes SHA256 por
arquivo relevante, hash agregado e exclusoes de artefatos runtime. O proprio
manifesto e excluido do hash para evitar autoreferencia.

## Bitradex Dockerfile

`bitradex_realtime_candle_collector_v1/Dockerfile` faz parte do escopo de
infraestrutura de dados. Ele agora possui usuario nao-root, permissoes para
`/app/data` e `/app/logs`, `PLAYWRIGHT_BROWSERS_PATH` compartilhado e
`HEALTHCHECK` de import seguro.

## Paper/shadow only

As flags seguem bloqueadas:

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false
```

Esta branch nao tenta desbloquear soak, readiness gate, Monte Carlo ou live. Os
bloqueios operacionais atuais continuam sendo gates corretos.

## Validacao local

```bash
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_complete_ci_security_typecheck_runtime_readiness.py -q
python -m pytest -q
make lint
make typecheck
make security
make audit
make paper-check
```
