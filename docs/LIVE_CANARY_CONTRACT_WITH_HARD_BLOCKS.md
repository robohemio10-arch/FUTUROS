# Live Canary Contract With Hard Blocks

Esta branch define um contrato read-only de canário com hard blocks. O contrato não executa trading, não envia ordens, não altera risco, não altera modelos e não promove release.

## Arquivos

- `smartcrypto/ops/live_canary_contract.py`
- `scripts/audit_live_canary_contract.py`
- `tests/test_live_canary_contract_with_hard_blocks.py`
- `data/reports/live_canary_contract_with_hard_blocks.json` quando a CLI roda com escrita habilitada

## Contrato canônico

```text
capital global: 20–50 USDT
capital por símbolo: 10 USDT
símbolos: BTC/USDT e ETH/USDT
max_safety_orders=0
martingale_multiplier=1.0
market_buy bloqueado
market order bloqueado
preferência LIMIT_MAKER
kill switch obrigatório
reconciliação obrigatória
rollback obrigatório
observabilidade obrigatória
```

## Invariantes

Mesmo quando o contrato está definido:

```text
manual_go_no_go_required=true
hard_blocks_enforced=true
auto_promotion_allowed=false
release_allowed=false
live_release_allowed=false
canary_release_allowed=false
changes_risk=false
sends_orders=false
```

## Uso

Sem escrita:

```powershell
python .\scripts\audit_live_canary_contract.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_live_canary_contract.py
```

Com validação de um candidato explícito:

```powershell
python .\scripts\audit_live_canary_contract.py `
  --candidate-config-path data\governance\candidate_canary_config.json `
  --json
```

## Dependência de governança manual

O contrato exige o relatório:

```text
data/reports/manual_go_no_go_live_canary_governance.json
```

Somente `status=manual_go_recorded` e `manual_decision=GO` passam na validação de governança. Mesmo assim, o contrato continua com `release_allowed=false`; ele apenas materializa limites e hard blocks para auditoria.

## Fora de escopo

Esta branch não executa canário. Ela define o contrato técnico auditável para uma etapa posterior controlada.
