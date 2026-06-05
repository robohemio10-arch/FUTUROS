# Docker Healthcheck Backup Restore Runbooks

Esta frente adiciona healthcheck, snapshot de backup e restore dry-run para o ambiente Docker/paper/shadow do FUTUROS/SmartCrypto.

Tudo aqui e operacional e read-only em relacao a trading real. Nenhum comando habilita live, envia ordens, acessa exchange privada, promove modelo ou altera Freqtrade DB, `trades_master`, `training_dataset.parquet`, registry, modelos, signal producer, Docker ou `.env`.

## System Healthcheck

O healthcheck consolida evidencias locais do runtime paper/shadow:

- `readiness_gate_report.json`
- `paper_soak_report.json`
- `critical_alerting_report.json`
- `risk_recovery_mode_audit_report.json`
- `market_data_health_audit_report.json`
- `state_reconciliation_audit_report.json`
- `order_intent_capital_ledger_audit_report.json`
- `backup_snapshot_report.json`
- `restore_dry_run_report.json`
- `Dockerfile`, quando existir
- `docker-compose.paper.yml`, quando existir

Ele verifica existencia, freshness, status bloqueado, flags de seguranca, modo de risco, reconciliacao, ledger e presenca de healthcheck documentado em Dockerfile/compose.

Comando:

```powershell
python scripts/run_system_healthcheck.py --max-report-age-seconds 900
```

Modo estrito:

```powershell
python scripts/run_system_healthcheck.py --max-report-age-seconds 900 --strict
```

Saida padrao:

```text
data/reports/system_healthcheck_report.json
```

Status possiveis:

- `ok`: evidencias criticas presentes, frescas e sem bloqueios;
- `warning`: fonte auxiliar ausente, stale ou healthcheck Docker nao documentado;
- `missing_data`: fonte critica ausente em modo nao estrito;
- `blocked`: flag insegura, readiness/alerting/recovery/reconciliation/ledger bloqueado, backup/restore obrigatorio ausente em modo estrito ou healthcheck critico ausente em modo estrito.

## Backup Snapshot

O snapshot copia apenas inputs explicitamente informados e gera manifesto com SHA256.

Comando:

```powershell
python scripts/run_backup_snapshot.py `
  --inputs docs config `
  --output-dir data/backups/system_snapshot_YYYYMMDD_HHMMSS `
  --report data/reports/backup_snapshot_report.json
```

O backup recusa por padrao:

- `.env`;
- arquivos com `secret`, `token`, `credential`, `private_key` ou `id_rsa` no caminho;
- arquivos `.key`, `.pem`, `.p12`, `.pfx`;
- DB Freqtrade (`.sqlite`/`.db` com tokens de Freqtrade) sem `--allow-freqtrade-db`.

O manifesto fica em:

```text
<backup-dir>/backup_manifest.json
```

Politica de caminhos:

- arquivos dentro do repositorio sao registrados pelo caminho relativo ao project root, por exemplo `docker/dashboard/Dockerfile`, `docker/qlib/Dockerfile` e `docker/smartcrypto/Dockerfile`;
- arquivos fora do repositorio usam namespace externo seguro `external/<hash>/<basename>` ou `external/<hash>/<input-dir>/<path-interno>`;
- `files[].relative_path` nunca deve se repetir no manifesto;
- se um manifesto externo ou legado contiver `relative_path` duplicado, o restore dry-run bloqueia com `duplicate_relative_paths`;
- se o backup detectar colisao antes de escrever, ele bloqueia em vez de gerar snapshot ambiguo.

Campos principais:

- `file_count`;
- `total_size_bytes`;
- `files[].relative_path`;
- `files[].source_path`;
- `files[].sha256`;
- `files[].size_bytes`;
- safety flags paper/shadow.

## Restore Dry-Run

O restore e sempre dry-run. Ele valida manifesto e hashes, lista arquivos que seriam restaurados e nunca sobrescreve arquivos reais.

Comando:

```powershell
python scripts/run_restore_dry_run.py `
  --backup-dir data/backups/system_snapshot_YYYYMMDD_HHMMSS `
  --report data/reports/restore_dry_run_report.json
```

Tambem e possivel informar o manifesto diretamente:

```powershell
python scripts/run_restore_dry_run.py `
  --manifest data/backups/system_snapshot_YYYYMMDD_HHMMSS/backup_manifest.json
```

## Sequencia Operacional

1. Gerar backup snapshot apenas de paths permitidos:

```powershell
python scripts/run_backup_snapshot.py --inputs docs config --output-dir data/backups/system_snapshot_YYYYMMDD_HHMMSS
```

Exemplo para evidencias runtime e Dockerfiles institucionais:

```powershell
python scripts/run_backup_snapshot.py `
  --inputs data/reports/critical_alerting_report.json data/reports/market_data_health_audit_report.json data/reports/state_reconciliation_audit_report.json data/reports/order_intent_capital_ledger_audit_report.json data/reports/risk_recovery_mode_audit_report.json data/reports/runtime_evidence_refresh_report.json data/reports/system_healthcheck_report.json data/runtime/runtime_safety_audit_config.json docker-compose.paper.yml docker/smartcrypto/Dockerfile docker/dashboard/Dockerfile docker/qlib/Dockerfile `
  --output-dir data/backups/runtime_evidence_latest `
  --report data/reports/backup_snapshot_report.json
```

2. Validar restore dry-run:

```powershell
python scripts/run_restore_dry_run.py --backup-dir data/backups/system_snapshot_YYYYMMDD_HHMMSS
```

3. Rodar healthcheck:

```powershell
python scripts/run_system_healthcheck.py --strict
```

## Garantias

- Paper/shadow only.
- Live trading permanece desabilitado.
- `ORDER_SUBMISSION_ENABLED` e `REAL_ORDER_SUBMISSION_ENABLED` permanecem false.
- Nenhum acesso privado a exchange.
- Nenhuma ordem real ou simulada e enviada por estes scripts.
- Nenhum DB operacional do Freqtrade e alterado.
- `trades_master` e `training_dataset.parquet` nao sao alterados.
- Registry, modelos, Qlib runtime e signal producer nao sao alterados.
- `.env` nao e lido como configuracao operacional nem copiado para backup.
- Relatorios em `data/reports/` e backups em `data/backups/` sao runtime e nao devem ser versionados.
