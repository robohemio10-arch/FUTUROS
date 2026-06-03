# Idempotent Order Intent And Capital Ledger

Esta camada cria um ledger institucional para intenções de ordem e reserva de
capital em modo paper/shadow. Ela não envia ordens reais, não chama exchange,
não toca no DB operacional do Freqtrade e não altera datasets, modelos,
registry, signal producer ou runtime Qlib.

## Objetivo

O objetivo é garantir que uma decisão operacional paper/shadow possua:

- `correlation_id`;
- `idempotency_key`;
- `client_order_id` determinístico;
- reserva de capital antes de submit simulado;
- bloqueio de duplicidade;
- bloqueio de double spend;
- transições de status válidas;
- tratamento formal para timeout e `DISPATCH_UNKNOWN`;
- auditoria read-only com relatório JSON controlado.

## Arquivos

- `smartcrypto/execution/order_intent_ledger.py`
- `smartcrypto/execution/capital_reservation_ledger.py`
- `scripts/run_order_intent_capital_ledger_audit.py`

Os ledgers usam SQLite local configurável. O caminho padrão é:

`data/runtime/order_intent_capital_ledger.sqlite`

Esse arquivo é runtime e não deve ser versionado.

## OrderIntentLedger

Cada intenção contém:

- `order_intent_id`
- `correlation_id`
- `client_order_id`
- `idempotency_key`
- `symbol`
- `side`
- `order_type`
- `requested_notional`
- `requested_quantity`
- `requested_price`
- `reserved_capital`
- `leverage`
- `risk_decision_id`
- `risk_mode`
- `status`
- `created_at_utc`
- `updated_at_utc`
- `state_before`
- `state_after`
- `reason`
- flags paper/shadow only

Status aceitos:

- `CREATED`
- `CAPITAL_RESERVED`
- `SIMULATED_SUBMITTED`
- `SIMULATED_ACKNOWLEDGED`
- `PARTIALLY_FILLED`
- `FILLED`
- `CANCELLED`
- `REJECTED`
- `TIMEOUT`
- `DISPATCH_UNKNOWN`
- `RECONCILIATION_REQUIRED`

O `client_order_id` é determinístico a partir da `idempotency_key`, salvo quando
um valor explícito for informado. A duplicidade é bloqueada por
`client_order_id`, `idempotency_key` ativa e, quando configurado, por
`correlation_id + symbol + side` dentro de uma janela temporal.

Timeout após submit simulado marca `DISPATCH_UNKNOWN`. Enquanto houver
`DISPATCH_UNKNOWN`, novas intenções para o mesmo símbolo/lado são bloqueadas até
reconciliação.

## CapitalReservationLedger

Cada reserva contém:

- `reservation_id`
- `order_intent_id`
- `client_order_id`
- `symbol`
- `quote_asset`
- `reserved_amount`
- `consumed_amount`
- `released_amount`
- `status`
- `created_at_utc`
- `updated_at_utc`
- `reason`

Status aceitos:

- `RESERVED`
- `PARTIALLY_CONSUMED`
- `CONSUMED`
- `RELEASED`
- `CANCELLED_RELEASED`
- `REJECTED_RELEASED`
- `EXPIRED`
- `RECONCILIATION_REQUIRED`

Bloqueios:

- reserva negativa ou zero;
- consumo maior que reservado;
- liberação maior que saldo reservado;
- duas reservas ativas para mesma `idempotency_key`;
- double spend do mesmo capital;
- capital global insuficiente;
- capital por símbolo insuficiente;
- flags de segurança inseguras.

Partial fill consome capital proporcional e preserva o saldo restante reservado.
Cancelamento libera o restante. Rejeição libera tudo. Fill total consome o saldo
restante e zera a reserva.

## Auditoria

Relatório padrão:

`data/reports/order_intent_capital_ledger_audit_report.json`

Campos principais:

- `status`
- `reason`
- `generated_at_utc`
- `repository_path`
- `order_intents_count`
- `capital_reservations_count`
- `active_intents_count`
- `active_reservations_count`
- `duplicate_client_order_id_count`
- `duplicate_idempotency_key_count`
- `dispatch_unknown_count`
- `double_spend_findings`
- `negative_reservation_findings`
- `over_consumption_findings`
- `invalid_transition_findings`
- `reconciliation_required`
- `recommended_mode`
- `blocking_findings`
- `warnings`
- flags paper/shadow only

Status:

- `ok`
- `warning`
- `blocked`
- `missing_data`

## Uso

Auditoria padrão:

```powershell
python .\scripts\run_order_intent_capital_ledger_audit.py `
  --repository .\data\runtime\order_intent_capital_ledger.sqlite
```

Auditoria com relatório explícito:

```powershell
python .\scripts\run_order_intent_capital_ledger_audit.py `
  --repository .\data\runtime\order_intent_capital_ledger.sqlite `
  --report .\data\reports\order_intent_capital_ledger_audit_report.json `
  --strict
```

Uso programático:

```python
from smartcrypto.execution.order_intent_ledger import OrderIntentLedger

ledger = OrderIntentLedger("data/runtime/order_intent_capital_ledger.sqlite")
intent = ledger.create_intent(
    correlation_id="corr-1",
    idempotency_key="decision-abc",
    symbol="BTCUSDT",
    side="long",
    requested_notional=100,
)
ledger.reserve_capital(intent.order_intent_id)
ledger.submit_simulated(intent.order_intent_id)
```

## Garantias De Segurança

- Paper/shadow only.
- Não habilita live trading.
- Não habilita `ORDER_SUBMISSION_ENABLED`.
- Não habilita `REAL_ORDER_SUBMISSION_ENABLED`.
- Não acessa exchange privada.
- Não envia ordens.
- Não toca no DB operacional do Freqtrade.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não altera signal producer.
- Não altera runtime Qlib.
- Não altera registry.
- Não promove modelos.
- Não altera Docker.
- Não altera `.env`.

O ledger registra intenção, reserva e lifecycle simulado. Ele não é um executor
de ordens reais.
