# Security audit exceptions

Esta branch nao possui excecoes `pip-audit` ativas. O lock direto de
desenvolvimento/teste e auditado por `make security` com:

```bash
python -m pip_audit -r requirements-dev.lock --no-deps --disable-pip --progress-spinner off
```

O gate ativo de Bandit tambem nao usa `# nosec` nem `--skip`. Ele e incremental
e cobre somente o escopo institucional atual:

```bash
python -m bandit -q -r smartcrypto/runtime smartcrypto/ops/backup_restore.py smartcrypto/ops/system_healthcheck.py scripts/generate_project_manifest.py scripts/scan_versioned_secrets.py --severity-level medium --confidence-level medium
```

## Accepted legacy debt

As entradas abaixo nao sao excecoes ativas do gate atual. Elas documentam
achados conhecidos do scan amplo bruto `bandit -q -r smartcrypto scripts`, que
permanece fora do escopo desta branch para evitar refatoracao funcional em massa.
Qualquer expansao do escopo de Bandit deve resolver ou reclassificar estas linhas
antes de inclui-las no gate.

| Date | Classification | Rule/advisory | Package | Reason | Plan |
| --- | --- | --- | --- | --- | --- |
| 2026-06-06 | accepted_legacy_debt | B608, possible SQL injection vector through string-based query construction | n/a | Modulos legados constroem SQL com nomes de tabela/coluna controlados por contrato interno. O risco precisa ser revisado por modulo antes de ampliar o gate. | Criar branch dedicada para validar identificadores SQL, centralizar allowlists de tabelas e remover o item do backlog. |
| 2026-06-06 | accepted_legacy_debt | B310, urllib/urlopen audit finding | n/a | Coletores historicos e rotinas de market data usam endpoints publicos sem credenciais, mas ainda nao possuem wrapper comum de allowlist de URL. | Criar wrapper de HTTP publico com allowlist de scheme/host e ampliar o escopo do Bandit para os coletores. |

Arquivos representativos do backlog B608:

- `scripts/audit_ai_shadow_filter_decision_db.py`
- `scripts/collect_phase3_summary.py`
- `scripts/inspect_phase3_outputs.py`
- `scripts/inspect_phase5_outputs.py`
- `scripts/run_ai_shadow_filter_incremental_daily.py`
- `scripts/run_order_intent_capital_ledger_audit.py`
- `smartcrypto/execution/order_intent_ledger.py`
- `smartcrypto/execution/paper_force_close.py`
- `smartcrypto/ml/ai_shadow_outcome_attribution.py`
- `smartcrypto/state/state_repository.py`

Arquivos representativos do backlog B310:

- `scripts/audit_bitradex_15s_complete_minutes_v4.py`
- `scripts/download_phase22_historical_candles.py`
- `smartcrypto/market_data/health_runtime_sources.py`
- `smartcrypto/qlib_engine/market_features_refresh.py`

## Safety policy

Nenhuma excecao de seguranca pode habilitar live trading, envio de ordens,
acesso privado a exchange, alteracao de Freqtrade DB ou escrita em artefatos de
runtime versionados. High severity em escopo ativo bloqueia a branch; medium/low
fora do escopo ativo devem aparecer aqui com data, regra/advisory, motivo e plano.
