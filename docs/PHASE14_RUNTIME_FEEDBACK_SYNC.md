# Phase 14 Runtime Feedback Sync — paper/shadow only

## Objetivo

Automatizar o ciclo paper-only da Fase 14 para manter o dashboard, relatórios e feedback fechado sincronizados a partir de snapshot local do SQLite Freqtrade, sem escrever no banco operacional do Freqtrade e sem qualquer acesso a exchange privada.

Este documento registra o estado canônico do serviço `phase14-feedback-sync-paper`, já presente no `docker-compose.paper.yml`, e do runner `scripts/run_phase14_runtime_feedback_sync.py`.

## Escopo operacional

O serviço executa periodicamente:

1. exportação read-only do SQLite operacional paper para snapshot local;
2. inspeção de posições abertas;
3. coleta de feedback de trades fechados;
4. inspeção dos outputs da Fase 14;
5. geração do summary consolidado de runtime sync.

## Arquivos canônicos

| Tipo | Caminho |
|---|---|
| Runner | `scripts/run_phase14_runtime_feedback_sync.py` |
| Compose service | `docker-compose.paper.yml` → `phase14-feedback-sync-paper` |
| Testes | `tests/test_phase14_runtime_feedback_sync_service.py` |
| Snapshot export report | `data/reports/freqtrade_paper_db_snapshot_export.json` |
| Runtime sync report | `data/reports/phase14_runtime_feedback_sync_report.json` |
| Open positions report | `data/reports/phase14_open_positions_report.json` |
| Closed feedback report | `data/reports/phase14_closed_feedback_report.json` |
| Output summary | `data/reports/phase14_output_summary.json` |
| Summary | `data/reports/phase14_summary.json` |

## Contrato de segurança

O serviço deve preservar:

```json
{
  "runtime_mode": "paper",
  "paper_only": true,
  "shadow_only": true,
  "live_trading_enabled": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "exchange_private_access": false
}
```

Proibições absolutas:

- não habilitar live;
- não habilitar canary;
- não enviar ordens;
- não chamar `create_order`;
- não usar `ccxt`;
- não chamar RPC do Freqtrade;
- não chamar exchange privada;
- não ler saldo privado;
- não alterar risco;
- não alterar `.env`;
- não escrever no SQLite operacional do Freqtrade.

## Compose service

Serviço canônico:

```yaml
phase14-feedback-sync-paper:
  build:
    context: .
    dockerfile: docker/smartcrypto/Dockerfile
  restart: unless-stopped
  environment:
    SMARTCRYPTO_RUNTIME_MODE: paper
    LIVE_ENABLED: "false"
    ORDER_SUBMISSION_ENABLED: "false"
    REAL_ORDER_SUBMISSION_ENABLED: "false"
    SMARTCRYPTO_EXCHANGE_PRIVATE_ACCESS: "false"
    SMARTCRYPTO_PHASE14_OPERATIONAL_DB_PATH: /paper-db/tradesv3.paper.sqlite
    SMARTCRYPTO_FREQTRADE_DB_PATH: /app/data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite
    PYTHONPATH: /app
  working_dir: /app
  volumes:
    - ./data:/app/data
    - ./config:/app/config:ro
    - ./scripts:/app/scripts:ro
    - ./smartcrypto:/app/smartcrypto:ro
    - freqtrade_paper_db:/paper-db:ro
  command: python scripts/run_phase14_runtime_feedback_sync.py --source-db /paper-db/tradesv3.paper.sqlite --interval-seconds 120
```

O volume `freqtrade_paper_db:/paper-db:ro` é obrigatório. O runner deve ler o banco operacional como fonte read-only e escrever somente artefatos derivados em `data/`.

## CLI

Execução única local:

```powershell
python .\scripts\run_phase14_runtime_feedback_sync.py `
  --source-db "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite" `
  --snapshot-output "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite" `
  --snapshot-report "data/reports/freqtrade_paper_db_snapshot_export.json" `
  --report "data/reports/phase14_runtime_feedback_sync_report.json" `
  --once
```

Execução periódica:

```powershell
python .\scripts\run_phase14_runtime_feedback_sync.py `
  --source-db "data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite" `
  --interval-seconds 120
```

No container, a fonte canônica é `/paper-db/tradesv3.paper.sqlite`, montada como read-only.

## Status report

O runner deve gerar `data/reports/phase14_runtime_feedback_sync_report.json` com, no mínimo:

- `status`;
- `reason`;
- `source_db`;
- `source_db_read_only`;
- `snapshot_output`;
- `snapshot_status`;
- `snapshot_reason`;
- `open_positions_status`;
- `open_rows`;
- `closed_feedback_status`;
- `closed_rows`;
- `raw_rows`;
- `output_summary_status`;
- `phase14_summary_status`;
- `reports_generated`;
- `dashboard_inputs_refreshed`;
- `created_at`;
- safety flags paper/shadow.

## Critérios de bloqueio

O runtime sync deve retornar `blocked` quando:

- snapshot export falha;
- open positions report retorna `blocked`;
- closed feedback report retorna `blocked`.

O serviço não deve tentar corrigir banco operacional, reconciliar via exchange privada, enviar ordem, cancelar ordem ou alterar risco.

## Validação obrigatória

```powershell
python -m compileall -q smartcrypto scripts tests
python -m pytest -q tests/test_phase14_runtime_feedback_sync_service.py
python -m pytest -q tests/test_phase14_sqlite_snapshot_reader.py
python -m pytest -q tests/test_dashboard_freqtrade_snapshot_reader.py
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
docker compose -f .\docker-compose.paper.yml config
```

## Definition of Done

- serviço `phase14-feedback-sync-paper` presente no compose;
- runner executa ciclo inicial imediatamente;
- `--once` disponível para teste;
- `--interval-seconds` disponível para operação contínua;
- SQLite operacional montado read-only;
- snapshot local atualizado;
- open positions report gerado;
- closed feedback report gerado;
- output summary gerado;
- summary consolidado gerado;
- dashboard pode consumir snapshot atualizado;
- testes específicos passam;
- manifest check passa;
- secret scan passa;
- safety flags preservadas;
- nenhuma ordem enviada;
- nenhum risco alterado.
