# Transitive Lock Docker Runtime Reproducibility

## Objetivo

Esta branch fecha a reproducibilidade institucional do ambiente FUTUROS /
SmartCrypto. O projeto continua paper/shadow only: nao habilita live trading,
nao envia ordens, nao acessa exchange privada, nao altera Freqtrade DB, modelos,
datasets, stake ou leverage.

## Fonte De Verdade Do Lock

Os locks versionados sao:

- `requirements-runtime.lock`: lock transitivo usado pelos containers
  SmartCrypto paper e dashboard.
- `requirements-dev.lock`: lock transitivo usado pelo CI e pelo ambiente local
  de teste/desenvolvimento.
- `constraints.txt`: constraints compartilhadas para workers opcionais que
  precisam de requisitos diretos adicionais, como Qlib.

Os locks devem conter dependencias pinadas com `==`. Ranges abertos ficam no
`pyproject.toml` apenas como metadados de pacote e extras de desenvolvimento,
nao como fonte de instalacao do Docker/CI.

## Ambiente Local

Com Makefile:

```bash
make install
make compile
make test
```

Sem Makefile:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m compileall scripts smartcrypto tests
python -m pytest -q
```

O `--no-deps` na instalacao editavel e intencional: as dependencias ja foram
instaladas pelo lock transitivo. Isso impede que ranges do `pyproject.toml`
resolvam versoes diferentes depois do lock.

## Docker

Os Dockerfiles SmartCrypto e dashboard instalam:

```bash
python -m pip install -r requirements-runtime.lock
python -m pip install --no-deps -e .
```

O Dockerfile Qlib instala os requisitos diretos de `docker/qlib/requirements.txt`
com `-c requirements-runtime.lock -c constraints.txt`, preservando pins
compartilhados e `pyqlib==0.9.7`.

Todos os Dockerfiles preservam usuario nao-root e `HEALTHCHECK`. Nenhum
Dockerfile habilita live trading ou order submission.

## CI

O workflow `.github/workflows/ci.yml` instala:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
```

Depois roda compileall, lint, typecheck, security, manifest check, secret scan,
pytest completo, builds Docker e smoke test de healthcheck.

## Pip Audit

`make security` executa:

```bash
python -m pip_audit -r requirements-dev.lock --progress-spinner off
```

O comando nao usa `--no-deps` nem `--disable-pip`, evitando falsa cobertura de
seguranca. O objetivo e auditar o conjunto transitivo declarado pelo lock dev.

## Paper/Shadow Only

As flags institucionais continuam bloqueadas:

```text
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS=false
```

Esta branch nao altera estrategia, signal producer, Freqtrade DB, modelos,
registry, datasets, `trades_master`, `training_dataset.parquet`, stake ou
leverage.

## Artefatos Nao Versionados

Continuam proibidos no git:

- `data/`
- `reports/`
- `logs/`
- `evidence/`
- `models/`
- `*.sqlite`, `*.db`, `*.parquet`, `*.csv`, `*.xlsx`, `*.jsonl`

## Validacao

```bash
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_transitive_lock_docker_runtime_reproducibility.py -q
python -m pytest -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

Build Docker opcional:

```bash
docker compose -f docker-compose.paper.yml config
docker build -f docker/smartcrypto/Dockerfile -t smartcrypto-paper-lock-test .
```
