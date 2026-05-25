# Segurança Operacional

## Padrões obrigatórios

- Nunca versionar chaves reais.
- Usar API sem saque.
- Usar `.env` local.
- Começar com `dry_run=true`.
- Manter `LIVE_ENABLED=false` até Go/No-Go formal.
- Usar isolated margin.
- Usar leverage baixo.
- Bloquear sinal stale.
- Registrar todas as decisões do RiskManager.

## Variáveis críticas

```env
SMARTCRYPTO_RUNTIME_MODE=paper
LIVE_ENABLED=false
ORDER_SUBMISSION_ENABLED=false
REAL_ORDER_SUBMISSION_ENABLED=false
DASHBOARD_READONLY_DEFAULT=true
```
