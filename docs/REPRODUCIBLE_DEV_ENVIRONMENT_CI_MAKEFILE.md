# Ambiente Reproduzível de Desenvolvimento e CI

Esta frente adiciona uma camada mínima institucional para instalar, testar e auditar o FUTUROS/SmartCrypto em clone ou ZIP limpo.

Ela não altera lógica de trading, risco, IA, modelos, Freqtrade, stake, leverage, datasets ou artefatos runtime. O projeto continua paper/shadow only.

## Instalação Em Clone Limpo

Com Makefile:

```bash
make install
make compile
make test
```

Sem Makefile:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev,test]"
python -m compileall scripts smartcrypto tests
python -m pytest -q
```

## Dependência PyArrow

`pyarrow` é dependência explícita do extra `test` e do extra `dev`, além dos extras já existentes `data` e `dashboard`.

Isso evita falhas em ambiente externo quando testes ou scripts usam parquet via pandas.

## Lockfile

`requirements-dev.lock` registra pins conservadores do ambiente local validado.

Uso opcional para troubleshooting:

```bash
python -m pip install -r requirements-dev.lock
python -m pip install -e ".[dev,test]"
```

O `pyproject.toml` continua sendo a fonte principal de dependências. O lock completo com resolução transitive via ferramenta dedicada, como `uv lock`, permanece uma evolução operacional possível.

## Makefile

Targets disponíveis:

- `make install`
- `make compile`
- `make test`
- `make test-fast`
- `make lint`
- `make typecheck`
- `make security`
- `make audit`
- `make paper-check`
- `make clean-cache`

`lint` roda `ruff` quando disponível. `typecheck` roda `mypy` apenas se já estiver instalado localmente; não acessa rede.

## CI

O workflow `.github/workflows/ci.yml` executa:

- checkout;
- Python 3.12;
- instalação com `.[dev,test]`;
- validação de flags paper/shadow;
- `python -m compileall scripts smartcrypto tests`;
- `python -m pytest -q`;
- checagem de ausência de artefatos runtime versionados.

O CI não usa secrets, não acessa exchange privada, não sobe container live, não chama Freqtrade live e não envia ordens.

## Warnings Conhecidos

A suíte pode emitir warnings de:

- `sklearn`/`scipy` relacionados ao solver L-BFGS;
- parsing de timestamps em testes de qualidade de dados.

Esses warnings são conhecidos e não equivalem a autorização de live.

## Validação De Artefatos Runtime

Comando local:

```bash
git ls-files data models reports logs "*.parquet" "*.sqlite" "*.csv" "*.xlsx" "*.jsonl" "*.zip"
```

O resultado esperado é vazio para artefatos runtime.

## Segurança

As flags institucionais continuam:

- `SMARTCRYPTO_RUNTIME_MODE=paper`
- `LIVE_ENABLED=false`
- `ORDER_SUBMISSION_ENABLED=false`
- `REAL_ORDER_SUBMISSION_ENABLED=false`
- `EXCHANGE_PRIVATE_ACCESS=false`

Nenhum target do Makefile ou passo do CI libera live, aumenta risco, promove modelo, altera Freqtrade DB, altera `trades_master`, altera `training_dataset.parquet` ou envia ordens.
