# Kill Switch — Single Source of Truth

Este documento fixa a fonte única do kill switch no FUTUROS/SmartCrypto e
elimina a ambiguidade de path que existia entre os componentes de risco.

## Problema Anterior

O kill switch tinha dois arquivos paralelos:

- `data/runtime/kill_switch.json`
  - escrito pelo operador via `scripts/set_kill_switch.py`;
  - lido por `smartcrypto/risk/risk_manager.py` (`RiskManager`, `evaluate_risk`);
  - lido por `smartcrypto/risk/kill_switch_classifier.py`;
  - lido pelo dashboard (`smartcrypto/dashboard/app.py`);
  - referenciado em `config/paper_session.yml` como `kill_switch`.

- `data/runtime/kill_switch_guard.json`
  - lido por `smartcrypto/risk/kill_switch_guard.py`;
  - lido por `smartcrypto/runtime/preflight_orchestrator.py`.

Consequência crítica: o operador ativava o kill switch em
`kill_switch.json`, mas o `preflight_orchestrator` lia
`kill_switch_guard.json`. O preflight podia retornar `CLEAR` enquanto o
operador acreditava ter bloqueado a operação. Isso era um risco de
segurança real, não apenas cosmético.

## Path Canônico

A fonte única do kill switch é:

```text
data/runtime/kill_switch.json
