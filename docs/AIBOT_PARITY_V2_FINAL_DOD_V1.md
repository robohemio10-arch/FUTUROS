# AIBOT Parity V2 — Final Software DoD Audit V1

## Objetivo

Este pacote fecha o **W14** como auditoria determinística e somente leitura do Definition of Done de software do AIBOT Parity V2.

Ele **não** ativa Paper, não executa builders, não treina modelos, não acessa providers/exchange e não publica sinais.

## Separação obrigatória

`SOFTWARE_DOD=PASS` significa apenas que as entregas estruturais necessárias ao candidato Paper estão presentes e que as fronteiras fail-closed continuam explícitas.

Isso **não** significa edge financeiro provado e **não** autoriza Treatment no Freqtrade dry-run.

A saída permanece:

```text
paper_treatment_release_allowed=false
paper_activation_performed=false
operational_authority=false
writes_active_signals=false
signal_published=false
sends_orders=false
changes_risk=false
changes_model=false
exchange_private_access=false
live_release_allowed=false
canary_release_allowed=false
```

## Waves auditadas

O auditor exige evidência estática para W1–W9 e W12–W13.

- W1: Trader Master benchmark.
- W2: Research Council.
- W3: Market Intelligence + rematerialização point-in-time.
- W4: regime + ensemble/abstention.
- W5: Opportunity Book + portfolio allocator/remaining edge.
- W6: Portfolio of Alphas + Fleet.
- W7: Relative Value.
- W8: Execution Intelligence.
- W9: Risk Budget + Treasury research simulator.
- W10: permanece `BLOCKED_EXTERNAL` enquanto o gate de segurança Qlib estiver bloqueado. A mera presença de código Qlib nunca promove esse status para PASS.
- W11: `CONDITIONAL_NOT_RUN`; PPO/RL não é requisito para o primeiro candidato Paper.
- W12: integração read-only do Dashboard.
- W13: orquestrador E2E idempotente/fail-closed.
- W14: PASS somente quando as evidências anteriores e o contrato de segurança estiverem completos.

## Contrato fail-closed

O W14 inspeciona o contrato do orquestrador e bloqueia o closeout se encontrar autoridade operacional ativa ou se desaparecerem os marcadores de segurança exigidos.

O auditor não substitui os gates institucionais do CI (`compile`, lint, typecheck, security, institutional audit, full tests, Docker e healthcheck). Ele complementa esses gates com a matriz de DoD do AIBOT Parity V2.

## Execução

```bash
python scripts/audit_aibot_parity_v2_dod.py --project-root .
```

O comando imprime JSON determinístico e retorna código `0` quando o Software DoD passa; retorna `2` quando a evidência está bloqueada.

## Próxima etapa após W14

Somente depois de W14 mergeado e pós-merge CI GREEN pode ser criada a etapa separada:

```text
codex/aibot-parity-paper-ab-soak-v1
```

Essa etapa deverá acumular evidência prospectiva Control × Treatment, lineage até closed outcome, validação financeira e soak operacional antes de qualquer autorização de Treatment no Freqtrade dry-run.
