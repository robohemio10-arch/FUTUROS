# B06 — Paper A/B, Testnet, Chaos, Capacity e Readiness V2

## Objetivo

A B06 implementa o último gate de engenharia antes do soak contínuo de 30 dias
em paper/shadow. O pacote consolida cinco frentes:

1. comparação Paper A/B entre champion e challengers;
2. contrato e validação de evidência E2E em exchange testnet;
3. harness isolado de testes de caos e recovery;
4. envelope de capacity e market impact;
5. preparação e inicialização documental do soak.

A B06 não ativa live/canary, não envia ordens reais, não altera RiskManager,
Freqtrade, Qlib ou IA Shadow ativos, não treina nem promove modelos e não
reinicia containers.

## Componentes

```text
smartcrypto/research/paper_ab_testnet_chaos_readiness/
├── capacity.py
├── chaos_harness.py
├── contracts.py
├── gates.py
├── io.py
├── paper_ab.py
├── readiness.py
├── soak.py
├── testnet_harness.py
└── writer.py
```

CLI:

```text
scripts/build_paper_ab_testnet_chaos_readiness_v2.py
```

Configuração:

```text
config/paper_ab_testnet_chaos_readiness_v2.json
```

## Execução padrão fail-closed

Sem evidência, a execução permanece bloqueada e não escreve arquivos:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --json
```

Resultado esperado:

```text
status=blocked
decision=BLOCKED_BEFORE_SOAK
ready_for_30_day_soak=false
```

## Contrato de evidência

O JSON de evidência usa:

```text
schema_version=paper_ab_testnet_chaos_evidence_v2
```

Estrutura mínima:

```json
{
  "schema_version": "paper_ab_testnet_chaos_evidence_v2",
  "prerequisites": {"g00_status": "PASS"},
  "paper_ab": {
    "champion": {
      "strategy_id": "champion-id",
      "evaluation_window_id": "shared-window-id",
      "trades": []
    },
    "challengers": []
  },
  "testnet_e2e": {"runs": []},
  "chaos": {"scenarios": []},
  "capacity": {"observations": []},
  "incidents": []
}
```

## Paper A/B

Champion e challengers devem usar o mesmo `evaluation_window_id` e fornecer,
por padrão, pelo menos 30 trades cada.

Campos obrigatórios por trade:

```text
trade_id
symbol
side
close_time_utc
net_pnl
notional
fees
funding
```

Métricas calculadas:

- quantidade de trades;
- wins, losses e breakeven;
- PnL líquido e bruto;
- profit factor;
- expectancy;
- hit rate;
- ganho e perda médios;
- payoff;
- drawdown máximo;
- turnover;
- custos totais e custo em basis points;
- estabilidade por semana ISO;
- proporção de períodos positivos;
- melhor e pior período;
- dispersão da expectancy temporal.

Uma recomendação de challenger é apenas advisory:

```text
action=QUARANTINE_CHALLENGER_FOR_SOAK|KEEP_CHAMPION
automatic_promotion=false
operational_authority=false
```

## Testnet E2E

A readiness final exige evidência produzida por exchange testnet real, nunca
mainnet/produção:

```text
evidence_class=exchange_testnet
environment=testnet
endpoint_class=testnet
testnet_order_submitted=true
real_order=false
active_runtime_touched=false
```

Etapas obrigatórias:

```text
signal_created
risk_approved
order_submitted_testnet
partial_fill_observed
cancel_observed
reconciliation_complete
restart_recovery_complete
```

### Harness isolado local

A opção abaixo executa um smoke determinístico e sem rede:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --evidence data/reports/paper_ab_testnet_chaos_evidence_v2.json `
  --run-isolated-testnet `
  --json
```

Esse harness valida o software local de signal → risk → order → partial fill →
cancel → reconciliation → restart/recovery. Ele usa
`evidence_class=isolated_harness` e, deliberadamente, **não satisfaz sozinho o
gate final de exchange testnet**.

O pacote não contém credenciais, SDK de exchange, chamada de rede ou autoridade
para enviar ordens.

## Caos e recovery

O harness isolado executa em diretório temporário:

```text
open_trade_restart
qlib_unavailable
signal_missing
sqlite_locked
disk_full
clock_skew
public_api_unavailable
corrupted_report
restart_loop
reconciliation_recovery
```

Comando:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --evidence data/reports/paper_ab_testnet_chaos_evidence_v2.json `
  --run-isolated-chaos `
  --json
```

Critérios por cenário:

```text
status=pass
data_loss=false
duplicate_orders=false
active_runtime_touched=false
recovery_seconds<=maximum_recovery_seconds
```

O harness não reinicia containers ativos. Restart, disco cheio, indisponibilidade
e locks são simulados em sandbox temporário.

## Capacity e market impact

Cada observação deve informar:

```text
observation_id
symbol
stake
notional
depth_usdt
leverage
participation_ratio
frequency_per_hour
turnover_per_day
spread_bps
slippage_bps
market_impact_bps
liquidation_buffer_pct
```

O gate valida:

- BTCUSDT e ETHUSDT separadamente;
- stake × leverage compatível com notional;
- frequência e turnover positivos;
- participation ratio;
- leverage máxima;
- buffer de liquidação;
- spread + slippage + impact;
- quantidade mínima de observações.

A saída contém envelope advisory por símbolo:

- safe notional;
- safe stake na leverage máxima configurada;
- leverage máxima observada;
- frequência máxima observada;
- turnover máximo observado;
- custo máximo observado;
- market impact máximo observado.

Nenhuma recomendação é aplicada ao RiskManager ou ao Freqtrade.

## Readiness e soak

A decisão final é:

```text
READY_FOR_30_DAY_SOAK
```

somente quando passam:

1. G00;
2. Paper A/B;
3. exchange testnet E2E;
4. caos/recovery;
5. capacity/market impact;
6. ausência de P0/P1 aberto.

Caso contrário:

```text
BLOCKED_BEFORE_SOAK
```

O plano do soak exige no mínimo 30 dias e as métricas:

```text
uptime
freshness
trades
gaps
duplicates
missed_signals
feedback_completeness
drift
drawdown
containers
notifications
```

Após readiness completa, o estado inicial pode ser persistido explicitamente:

```powershell
python scripts/build_paper_ab_testnet_chaos_readiness_v2.py `
  --project-root . `
  --evidence data/reports/paper_ab_testnet_chaos_evidence_v2.json `
  --initialize-soak `
  --write-report `
  --json
```

Essa operação apenas cria estado advisory em `data/reports/soak`. Ela não cria
scheduler, não inicia serviço e não altera runtime ou containers.

## Writer

Relatórios e estado do soak usam o writer atômico da B01, restrito a
`data/reports`, com JSON sem NaN. O modo padrão continua no-write.

## Invariantes de segurança

```text
research_only=true
paper_only=true
shadow_only=true
operational_authority=false
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
testnet_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
writes_runtime=false
restarts_containers=false
runs_training=false
promotes_model=false
automatic_promotion=false
model_promotion_performed=false
active_model_changed=false
writes_active_registry=false
writes_active_signals=false
updates_freqtrade=false
updates_risk_manager=false
updates_qlib_runtime=false
updates_ai_shadow_runtime=false
starts_soak=false
```

## Estado após implementação

A implementação de software da B06 pode ser concluída e validada sem declarar
readiness operacional. A decisão permanecerá bloqueada até o projeto fornecer
evidência paper A/B real, exchange testnet E2E real, capacity observada e todos
os demais gates aprovados.
