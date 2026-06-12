# Standalone Audit and Dockerignore Hardening v1

## Objetivo

Este hardening torna as auditorias de manifesto e secrets executáveis diretamente
em um clone ou ZIP do SMART FUTUROS, sem `PYTHONPATH` e sem instalação prévia do
pacote. Também reduz o contexto enviado ao Docker daemon por meio de um
`.dockerignore` institucional na raiz.

O trabalho é estritamente operacional e read-only. Não altera trading, risco,
Qlib, IA Shadow, OCR, datasets, sinais ativos, readiness, canary ou live.

## Auditoria standalone

Os comandos institucionais continuam:

```powershell
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

Os scripts tentam importar `smartcrypto.ops.versioned_file_discovery` normalmente.
Quando o pacote não está disponível no caminho de importação, carregam o arquivo
irmão do próprio snapshot. Antes de `exec_module`, o fallback registra o módulo em
`sys.modules`; isso preserva o contrato de `dataclasses` e evita falhas de resolução
de `cls.__module__`.

Se a execução não encontrar `.git`, a descoberta mantém a sequência conservadora:

1. `PROJECT_MANIFEST_CLEAN.json` como baseline, quando disponível;
2. filesystem walk limitado a arquivos versionáveis, quando não há baseline;
3. exclusão de dados runtime, caches, credenciais e formatos binários/proibidos.

Os testes executam cópias temporárias dos scripts com `PYTHONPATH` removido,
`PYTHONNOUSERSITE=1` e Python em modo isolado sem `site-packages`. Nenhum teste depende de rede,
Docker, exchange ou secrets.

## Proteção do build context

O `.dockerignore` exclui do contexto:

- `.env` e variantes, preservando `!.env.example`;
- certificados, chaves e arquivos de credenciais;
- `data/`, `logs/`, `reports/`, `runtime/`, `backups/`, `evidence/` e `models/`;
- SQLite, parquet, CSV, Excel, JSONL, logs e ZIPs;
- caches Python, cobertura, ambientes virtuais e metadados de IDE/Git.

O arquivo não altera Dockerfiles nem Compose. Ele apenas impede que arquivos locais
desnecessários ou sensíveis sejam enviados ao daemon durante o build.

## Validação

```powershell
python -m compileall -q scripts smartcrypto tests
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
python -m pytest tests/test_zip_standalone_audit_fallback.py -q
python -m pytest tests/test_dockerignore_build_context_safety.py -q
python -m pytest -q
```

Para confirmar que artefatos runtime não foram versionados:

```powershell
git ls-files | Select-String ".(parquet|sqlite|sqlite3|db|csv|xlsx|jsonl|zip)$"
```

## Segurança preservada

- `paper_only=true`
- `shadow_only=true`
- `live_trading_enabled=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`

Nenhum comando desta frente envia ordens, acessa exchange privada ou modifica
modelos, datasets, Freqtrade DB, risco ou arquivos runtime.
