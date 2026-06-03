# State Repository Reconciliation Hardening

Esta frente fortalece o estado financeiro persistente do SmartCrypto em modo
paper/shadow e adiciona um gate formal de reconciliação antes de novas decisões
operacionais.

O objetivo não é corrigir estado automaticamente. O objetivo é detectar
divergência, classificar o risco e bloquear novas intenções quando o estado
financeiro local não estiver confiável.

## Escopo

Arquivos principais:

- `smartcrypto/state/state_repository.py`
- `smartcrypto/state/reconciliation_guard.py`
- `scripts/run_state_reconciliation_audit.py`

O contrato antigo baseado em JSON continua preservado para compatibilidade com
`OrderManager` e preflight existentes. Quando o caminho do repositório termina
em `.sqlite`, `.sqlite3` ou `.db`, o `StateRepository` usa SQLite local
configurável.

## Estado Modelado

O repositório SQLite modela:

- `positions`
- `order_intents`
- `simulated_orders`
- `capital_reservations`
- `reconciliation_snapshots`
- `state_locks`
- `dispatch_locks`
- `audit_events`

Cada intenção de ordem inclui:

- `order_intent_id`
- `correlation_id`
- `client_order_id`
- `symbol`
- `side`
- `order_type`
- `requested_notional`
- `requested_quantity`
- `reserved_capital`
- `risk_decision_id`
- `state_before`
- `status`
- `created_at_utc`
- flags paper/shadow only

## Capital E Dispatch

Capital é reservado antes da intenção ser persistida como estado operacional.
A reserva é vinculada ao `client_order_id` e ao `reservation_id`.

Cancelamento ou rejeição libera a reserva, zerando o capital remanescente.
Partial fill ajusta `filled_notional` e `remaining_reserved_capital`.
Timeout após envio marca a intenção e a ordem simulada como `DISPATCH_UNKNOWN`.

Se existir `DISPATCH_UNKNOWN`, a reconciliação retorna `blocked` e recomenda
`RECONCILING`.

## Reconciliation Guard

O guard compara o estado local com um snapshot externo local quando fornecido.
Formatos aceitos para testes e auditoria:

- JSON
- JSONL
- CSV
- Parquet

O snapshot não é exchange privada. Ele deve ser um artefato local/simulado ou
export read-only já autorizado.

O guard detecta:

- posição local ausente no snapshot;
- posição do snapshot ausente localmente;
- divergência de quantidade;
- divergência de lado;
- `state_hash` divergente;
- capital reservado negativo;
- capital reservado sem ordem;
- ordem simulada sem `client_order_id`;
- duplicidade de `client_order_id`;
- `DISPATCH_UNKNOWN` ativo;
- partial fill inconsistente;
- locks expirados;
- flags de segurança inseguras.

## Relatório

Relatório padrão:

`data/reports/state_reconciliation_audit_report.json`

Campos principais:

- `status`
- `reason`
- `generated_at_utc`
- `repository_path`
- `snapshot_path`
- `reconciliation_required`
- `recommended_mode`
- `positions_count`
- `order_intents_count`
- `capital_reservations_count`
- `dispatch_unknown_count`
- `partial_fill_inconsistency_count`
- `duplicate_client_order_id_count`
- `negative_reserved_capital_count`
- `state_divergence_count`
- `blocking_findings`
- `warnings`
- flags paper/shadow only

Status:

- `ok`
- `warning`
- `blocked`
- `missing_data`

Modos recomendados:

- `NORMAL`
- `PROTECTION`
- `PANIC`
- `RECONCILING`

## Uso

Auditar somente o repositório local:

```powershell
python .\scripts\run_state_reconciliation_audit.py `
  --repository .\data\runtime\state_repository.sqlite
```

Auditar com snapshot local:

```powershell
python .\scripts\run_state_reconciliation_audit.py `
  --repository .\data\runtime\state_repository.sqlite `
  --snapshot .\data\snapshots\paper_positions_snapshot.json `
  --report .\data\reports\state_reconciliation_audit_report.json `
  --strict
```

Uso programático:

```python
from smartcrypto.state.reconciliation_guard import run_state_reconciliation_audit

report = run_state_reconciliation_audit(
    repository_path="data/runtime/state_repository.sqlite",
    snapshot_path="data/snapshots/paper_positions_snapshot.json",
)
if report["reconciliation_required"]:
    raise RuntimeError("Estado requer reconciliação")
```

## Garantias De Segurança

- Paper/shadow only.
- Não habilita live.
- Não habilita envio de ordens.
- Não acessa exchange privada.
- Não envia ordens.
- Não toca no DB operacional do Freqtrade.
- Não toca em `trades_master`.
- Não toca em `training_dataset.parquet`.
- Não altera signal producer.
- Não altera runtime Qlib.
- Não altera registry.
- Não promove modelo.
- Não altera modelos.
- Não altera Docker.
- Não altera `.env`.
- Não corrige estado automaticamente durante auditoria.

## Interpretação

`ok` significa que o estado local não apresentou divergência bloqueante.

`warning` indica achado não bloqueante, como lock expirado em contexto não
estrito.

`blocked` significa que novas decisões devem ser bloqueadas até reconciliação.
Os motivos ficam em `blocking_findings`.

`missing_data` indica que o repositório ou snapshot solicitado não está
disponível.
