# Manual Go/No-Go Live Canary Governance

Esta branch adiciona governança read-only para exigir decisão humana antes de qualquer etapa de canário.

O relatório gerado não executa trading, não altera risco, não altera datasets e não libera execução automática. Ele apenas valida a existência e a forma de uma decisão humana vinculada a evidências de readiness.

## Arquivos

- `smartcrypto/ops/manual_go_no_go_governance.py`
- `scripts/audit_manual_go_no_go_governance.py`
- `tests/test_manual_go_no_go_live_canary_governance.py`
- `data/reports/manual_go_no_go_live_canary_governance.json` quando a CLI roda com escrita habilitada

## Decisão humana

Arquivo esperado:

`data/governance/manual_go_no_go_live_canary_decision.json`

Modelo:

```json
{
  "decision": "GO",
  "decided_at": "2026-01-01T00:00:00Z",
  "decider": "human-operator-name",
  "evidence_pack_id": "evidence-pack-id",
  "rationale": "human-readable rationale",
  "restrictions": [],
  "acknowledges_risk": true,
  "acknowledges_no_automatic_release": true
}
```

Decisões aceitas:

- `GO`
- `NO_GO`
- `GO_WITH_RESTRICTIONS`
- `DEFER`

## Uso

Sem escrita:

```powershell
python .\scripts\audit_manual_go_no_go_governance.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_manual_go_no_go_governance.py
```

## Invariantes

Mesmo quando `GO` é registrado:

```text
manual_go_no_go_required=true
auto_promotion_allowed=false
release_allowed=false
live_release_allowed=false
canary_release_allowed=false
changes_risk=false
sends_orders=false
```

`GO_WITH_RESTRICTIONS` bloqueia até que as restrições humanas sejam convertidas em hard blocks no contrato operacional da branch seguinte.

## Fora de escopo

Esta branch não executa trading, não envia ordens, não altera risco, não altera datasets, não altera modelo e não libera canário/live.
