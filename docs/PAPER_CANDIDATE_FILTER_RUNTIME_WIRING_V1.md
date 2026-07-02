# Paper Candidate Filter Runtime Wiring V1

## Objetivo

Esta branch conecta o `PaperOnlyCandidateFilterAdapter` ao caminho real de
produção de sinais candidate/paper, sem alterar live, canary, Freqtrade,
RiskManager, Qlib, IA Shadow, modelos ou configs live.

O ponto integrado é:

- `smartcrypto/execution/signal_producer.py`
- função `build_active_signals`

O filtro é aplicado depois que as predições viram sinais propostos e antes de o
payload de sinais ser escrito/encaminhado para o fluxo paper candidate.

## Fluxo

```text
prediction row
-> row_to_signal
-> apply_paper_candidate_filter_to_signals
-> PaperOnlyCandidateFilterAdapter
-> ALLOW/BLOCK
-> BLOCK: removido antes de submissao candidate
-> ALLOW: permanece no payload candidate
```

## Escopo de ativação

O wiring só chama o adapter quando:

```text
runtime_mode=paper_candidate
```

Para qualquer outro modo:

- `runtime_wiring_status=disabled`
- `paper_candidate_filter_called=false`
- `filter_applied=false`
- sinais permanecem inalterados
- `live_behavior_changed=false`
- `canary_behavior_changed=false`

## Regras do filtro

- `ETHUSDT long`: `BLOCK`
- `ETHUSDT short`: `BLOCK`
- `BTCUSDT long`: `ALLOW`
- `BTCUSDT short`: `ALLOW`
- demais símbolos/sides: `ALLOW`

## Auditor

Comando:

```powershell
python .\scripts\audit_paper_candidate_filter_runtime_wiring_v1.py --project-root . --json
```

O auditor usa amostras fixas e valida:

- adapter chamado em `paper_candidate`;
- ETH long/short bloqueados antes da submissão candidate;
- BTC long/short permitidos;
- contadores de blocked/allowed;
- flags de segurança.

## Garantias

O wiring mantém:

- `live_behavior_changed=false`
- `canary_behavior_changed=false`
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

- live/canary/order real path;
- Docker ou compose;
- `.env`;
- configs live;
- RiskManager;
- Qlib runtime;
- IA Shadow runtime;
- modelos ou registry;
- active signals runtime existentes;
- `data/runtime`;
- SQLite;
- Parquet operacional.
