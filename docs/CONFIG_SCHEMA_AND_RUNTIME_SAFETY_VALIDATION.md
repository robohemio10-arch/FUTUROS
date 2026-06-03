# Config Schema And Runtime Safety Validation

Esta camada cria uma validação institucional de configuração para impedir que o
runtime SmartCrypto seja considerado seguro quando uma configuração estiver
incompleta, incompatível com o ambiente ou perigosa para a política
paper/shadow only.

Ela não altera `.env`, Docker, Freqtrade, banco operacional, registry, modelos,
datasets, sinais ou estado de runtime. O único arquivo gerado pela execução
operacional é o relatório JSON informado no CLI.

## Objetivo

O módulo `smartcrypto/config/runtime_safety_config.py` valida YAML, JSON ou um
`Mapping` Python antes de qualquer uso operacional. A validação cobre:

- modo de runtime compatível com o ambiente;
- versões de schema e config presentes;
- flags de segurança em paper/shadow only;
- dry-run obrigatório;
- bloqueio de live trading, envio de ordens e acesso privado à exchange;
- bloqueio de autoridade de IA ou dashboard para aumentar risco;
- limites mínimos de risco e saúde de mercado;
- limites absurdamente permissivos;
- warnings para limites acima do recomendado.

## Ambientes

Ambientes aceitos:

- `paper`
- `shadow`
- `backtest`
- `research`
- `live.example`

`live.example` existe apenas para validar exemplos/documentação de forma segura.
Ele não permite `runtime_mode=live`, não habilita ordem real e deve permanecer
em `dry_run=true`.

## Chaves Obrigatórias

Chaves de versão/runtime:

- `schema_version`
- `config_version`
- `runtime_mode`

Flags obrigatórias:

- `dry_run=true`
- `paper_only=true`
- `shadow_only=true`
- `kill_switch_enabled=true`

Limites obrigatórios:

- `max_drawdown_pct`
- `max_daily_loss_pct`
- `max_weekly_loss_pct`
- `max_consecutive_losses`
- `max_spread_bps`
- `max_slippage_bps`
- `max_latency_ms`
- `max_data_age_seconds`
- `stale_prediction_max_age_seconds`

## Flags Bloqueadas

Qualquer valor `true` nas flags abaixo bloqueia a configuração:

- `live_trading_enabled`
- `order_submission_enabled`
- `real_order_submission_enabled`
- `exchange_private_access`
- `sends_orders`
- `changes_risk`
- `ai_can_increase_risk`
- `ai_can_change_leverage`
- `ai_can_change_stake`
- `dashboard_can_change_risk`
- `dashboard_can_promote_model`
- `dashboard_can_enable_live`

## Relatório

O relatório padrão é:

`data/reports/runtime_safety_config_validation_report.json`

Campos principais:

- `status`
- `reason`
- `generated_at_utc`
- `config_path`
- `environment`
- `schema_version`
- `config_version`
- `runtime_mode`
- `blocking_findings`
- `warnings`
- `missing_required_keys`
- `unsafe_flags`
- `risk_limit_findings`
- `environment_findings`
- flags de segurança paper/shadow only

Status possíveis:

- `ok`
- `warning`
- `blocked`
- `invalid_schema`
- `missing_config`

Em modo `--strict`, qualquer warning é convertido em bloqueio.

## Uso

Validar config paper:

```powershell
python .\scripts\validate_runtime_safety_config.py --config .\config\paper.example.yml --environment paper
```

Validar config shadow com relatório explícito:

```powershell
python .\scripts\validate_runtime_safety_config.py `
  --config .\config\paper.example.yml `
  --environment shadow `
  --report .\data\reports\runtime_safety_config_validation_report.json `
  --strict
```

Uso programático:

```python
from smartcrypto.config.runtime_safety_config import validate_runtime_config

report = validate_runtime_config(config, environment="paper", strict=True)
if report["status"] != "ok":
    raise RuntimeError(report["reason"])
```

## Garantias De Segurança

- Não importa `ccxt`.
- Não importa clientes de exchange.
- Não chama Freqtrade.
- Não envia ordens.
- Não altera `.env`.
- Não altera Docker.
- Não altera configs automaticamente.
- Não altera `trades_master`.
- Não altera `training_dataset.parquet`.
- Não altera registry/modelos.
- Não altera signal producer.
- Não altera runtime Qlib.
- Escreve somente o relatório solicitado.

## Interpretação

`ok` significa que a configuração atende ao contrato institucional mínimo para
paper/shadow.

`warning` indica que a configuração está tecnicamente segura, mas algum limite
está mais permissivo que o recomendado. Em auditoria operacional, rode com
`--strict` para converter esse caso em `blocked`.

`blocked`, `invalid_schema` e `missing_config` devem impedir que a configuração
seja usada como fonte de readiness.
