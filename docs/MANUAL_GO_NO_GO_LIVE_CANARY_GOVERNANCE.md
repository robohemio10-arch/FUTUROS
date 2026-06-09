# Manual Go/No-Go Canary Governance

Esta branch adiciona governança read-only para exigir decisão humana antes de qualquer etapa de canário.

O relatório gerado não executa trading, não altera risco, não altera datasets e não libera execução automática. Ele apenas valida a existência e a forma de uma decisão humana vinculada a evidências de readiness.

## Contrato

- decisão humana obrigatória
- promoção automática proibida
- execução automática proibida
- relatório somente leitura
- evidências ausentes ou inválidas bloqueiam a governança

## Arquivos esperados

- `smartcrypto/ops/manual_go_no_go_governance.py`
- `scripts/audit_manual_go_no_go_governance.py`
- `tests/test_manual_go_no_go_live_canary_governance.py`

## Uso

```powershell
python .\scripts\audit_manual_go_no_go_governance.py --no-write --json
```

Com escrita padrão:

```powershell
python .\scripts\audit_manual_go_no_go_governance.py
```

## Decisões aceitas

- `GO`
- `NO_GO`
- `GO_WITH_RESTRICTIONS`
- `DEFER`

Mesmo quando `GO` é registrado, a próxima etapa obrigatória é a branch de contrato de canário com hard blocks.
