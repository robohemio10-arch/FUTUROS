# ZIP Standalone Audit Fallback

Esta camada permite que auditorias externas rodem o manifesto deterministico e o scan de segredos em um ZIP limpo do projeto, mesmo quando o diretorio `.git` nao esta presente.

## Fonte oficial

O modo oficial continua sendo Git/CI:

```powershell
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --json
```

Quando `.git` existe e `git ls-files` funciona, os scripts usam essa lista como fonte canonica de arquivos versionados.

## Fallback para ZIP

Sem `.git`, os scripts usam a seguinte ordem:

1. `PROJECT_MANIFEST_CLEAN.json` existente como baseline canonico.
2. Caminhada deterministica do filesystem com allowlist/denylist institucional.

O baseline e preferido porque um ZIP recebido de auditoria deve representar exatamente o conteudo versionado no manifesto. A caminhada do filesystem existe para diagnostico adicional quando o ZIP nao traz manifesto.

## Arquivos incluidos

O fallback inclui somente arquivos versionaveis de codigo, configuracao e documentacao:

- `scripts/`, `smartcrypto/`, `tests/`, `docs/`;
- workflows em `.github/workflows/`;
- `Dockerfile`, `Makefile`, lockfiles e `constraints.txt`;
- extensoes textuais como `.py`, `.md`, `.yml`, `.yaml`, `.toml`, `.txt`, `.ps1`, `.sh`, `.json` e `.lock`.

Todos os caminhos sao normalizados em formato POSIX (`path/to/file`) e ordenados de forma deterministica.

## Arquivos excluidos

O fallback nao trata artefatos runtime como versionados. Sao ignorados:

- `data/`, `reports/`, `logs/`, `models/`, `evidence/`;
- caches como `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`;
- `.env`, `.env.*` exceto `.env.example`;
- `*.sqlite`, `*.db`, `*.parquet`, `*.csv`, `*.xlsx`, `*.jsonl`, `*.log`, `*.zip`;
- chaves/certificados como `*.pem`, `*.key`, `*.crt`;
- symlinks durante a caminhada standalone.

## Como validar um ZIP

Extraia o ZIP em um diretorio temporario e rode:

```powershell
python .\scripts\generate_project_manifest.py --project-root <ZIP_EXTRAIDO> --check
python .\scripts\scan_versioned_secrets.py --project-root <ZIP_EXTRAIDO> --json
```

Resultado esperado:

- manifesto com `status=ok` e `reason=manifest_current`;
- secret scan com `status=ok`;
- flags de seguranca preservadas: `paper_only=true`, `shadow_only=true`, `live_trading_enabled=false`, `order_submission_enabled=false`, `real_order_submission_enabled=false`, `exchange_private_access=false`, `sends_orders=false`.

## Garantias de seguranca

Esta branch nao muda trading, risco, modelos, Freqtrade, datasets, stake ou leverage. Os scripts apenas leem arquivos locais versionaveis para auditoria. Eles nao acessam exchange privada, nao enviam ordens e nao habilitam live.
