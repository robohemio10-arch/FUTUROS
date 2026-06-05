# Dashboard Healthcheck Report Reclassification

Esta frente ajusta os inspectors e healthchecks read-only para diferenciar falha tecnica real de bloqueio conservador por politica.

## Problema

Alguns relatórios de dashboard e system health herdavam `status=blocked` de artefatos antigos ou de politicas de risco sem explicar a causa operacional atual. Isso fazia fontes atuais `ok`, como market health, drift e avaliação financeira, aparecerem como bloqueios stale.

## Classificacao

Bloqueios saudaveis por politica:

- `monte_carlo_no_trade_policy_active`
- `no_trade_policy_active`
- `readiness_may_proceed=false`
- `soak_days_below_required`

Falhas tecnicas reais:

- fonte obrigatoria ausente em modo strict;
- report stale com idade e fonte identificadas;
- market health realmente blocked;
- drift realmente blocked;
- financial thresholds realmente blocked;
- safety flags inseguras.

Warnings:

- fontes opcionais ausentes quando ha fonte alternativa valida;
- `missing_data` em ledger ou risk recovery;
- sample warning de trainer;
- paper/soak ainda abaixo do minimo.

## AI Governance

O painel passa a consumir `monte_carlo_risk_budget_policy_report`. Quando Monte Carlo estiver blocked, mas existir politica valida `policy_action=no_trade`, o risco é marcado como tratado:

- `monte_carlo_risk_treated=true`
- `no_trade_policy_present=true`
- `monte_carlo_risk_budget_policy_action=no_trade`

Isso nao aprova live, nao promove modelo e nao remove o bloqueio conservador.

## Risk Readiness Soak

O painel expoe:

- `monte_carlo_risk_treated`
- `no_trade_policy_present`
- `monte_carlo_risk_budget_policy_action`
- `readiness_may_proceed`
- `live_release_allowed=false`
- `stale_source_details`

`stale_data_count_above_limit` agora inclui fonte, timestamp, idade e limite quando a informação existir.

## System Healthcheck

O healthcheck é regeneravel a partir dos relatórios atuais. Ele nao mantém razões antigas quando a fonte atual esta `ok` ou `warning`. Bloqueios esperados ficam explícitos, como:

- `readiness_gate_blocked`
- `no_trade_policy_active`
- `soak_days_below_required`

## Garantias

- Paper/shadow only.
- Live trading permanece desabilitado.
- Order submission real permanece desabilitado.
- Nenhuma ordem é enviada.
- Exchange privada nao é acessada.
- RiskManager real, stake, leverage, modelos e registry nao sao alterados.
- Artefatos em `data/`, parquet, sqlite, csv, xlsx, logs e evidence continuam nao versionados.
