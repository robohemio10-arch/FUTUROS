# Paper-Only Candidate Filter Adapter V1

## Objetivo

Esta etapa remove o bloqueio `paper_adapter_missing` criando um adapter
explícito para o `PaperOnlyCandidateDecisionFilter`.

O adapter é isolado e só aplica o filtro quando chamado com
`mode=paper_candidate`. Em qualquer outro modo ele retorna `disabled` e não
altera comportamento.

## Fluxo localizado

O projeto já possui infraestrutura de execução e intents em `smartcrypto/execution`
e `smartcrypto/state`, incluindo order intent ledger, signal producer e order
manager. Esta branch não altera esses componentes porque o gate exige não tocar
live/canary/orders. O novo adapter fica em:

- `smartcrypto/execution/paper_candidate_filter_adapter.py`

Ele é uma peça explícita para ser chamada por um fluxo paper-candidate isolado
em branch futura, sem acoplamento automático ao Freqtrade.

## Comportamento

Entrada:

- proposta de trade/order intent paper;
- `mode`.

Saída:

- `ALLOW` ou `BLOCK`;
- evento estruturado;
- flags de segurança.

Regras:

- `mode != paper_candidate`: adapter desabilitado, `filter_applied=false`;
- `mode=live`: desabilitado com `adapter_rejects_live_mode`;
- `mode=canary`: desabilitado com `adapter_rejects_canary_mode`;
- `mode=paper_candidate`: filtro habilitado;
- `ETHUSDT long`: `BLOCK`;
- `ETHUSDT short`: `BLOCK`;
- `BTCUSDT long/short`: `ALLOW`;
- demais símbolos/sides: `ALLOW`.

## Auditor

Comando:

```powershell
python .\scripts\audit_paper_only_candidate_filter_adapter_v1.py --project-root . --json
```

O auditor usa amostras fixas em `paper_candidate`:

- ETHUSDT long;
- ETHUSDT short;
- BTCUSDT long;
- BTCUSDT short.

Ele não lê exchange, não chama Freqtrade, não envia ordens e não escreve runtime.

## Gate esperado

O JSON deve expor:

- `integration_status=paper_adapter_available`
- `paper_candidate_filter_enabled=true`
- `filter_applied=true`
- `blocked_eth_long_count >= 1`
- `blocked_eth_short_count >= 1`
- `live_behavior_changed=false`
- `canary_behavior_changed=false`
- `sends_orders=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `changes_risk=false`
- `writes_runtime=false`

## Segurança

O adapter preserva:

- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_sqlite=false`
- `writes_parquet=false`

## Fora de escopo

Esta branch não altera:

- Freqtrade;
- RiskManager;
- Qlib runtime;
- IA Shadow runtime;
- modelos;
- active signals;
- Docker;
- configs live;
- `.env`;
- SQLite;
- Parquet operacional;
- `data/runtime`.
