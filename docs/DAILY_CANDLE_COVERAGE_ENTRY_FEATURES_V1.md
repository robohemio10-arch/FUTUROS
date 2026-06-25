# Daily Candle Coverage Entry Features V1

## Objetivo

Esta branch cria a camada diária de candle coverage e entry features para o
Daily Paper/Master Learning Loop. O cálculo aceita apenas trades e candles já
fornecidos em memória por testes ou chamadas futuras controladas.

O relatório permanece:

- `status=blocked`
- `decision=MANTER_EM_RESEARCH`
- `research_only=true`
- `read_only=true`
- `operational_authority=false`

## Relação com Branches 01 a 05

Branch 01 fechou a investigação Paper vs `trades_master` como research-only.
Branch 02 definiu contratos e source map. Branch 03 criou loaders metadata-only.
Branch 04 criou o KPI pack agregado. Branch 05 adicionou divergence/alignment.
Esta Branch 06 calcula coverage e features pré-entrada em memória.

## Divergence/Alignment vs coverage/features

Divergence/alignment compara trades Paper e Master no tempo. Coverage/features
mede se existem candles pré-entrada suficientes e materializa features simples
usando somente candles anteriores ou iguais ao horário de entrada.

## Entradas em memória

Trade esperado:

- `trade_id`
- `symbol`
- `side`
- `open_time` ou `entry_time`
- `close_time` ou `exit_time`
- `entry_price`
- `exit_price`
- `net_pnl`
- `exit_reason`
- `duration_minutes`

Candle esperado:

- `symbol`
- `timestamp` ou `open_time`
- `open`
- `high`
- `low`
- `close`
- `volume`

O CLI não carrega arquivos reais. Não há flags para ler trades ou candles de
runtime nesta branch.

## Janelas e timeframe

Janelas default:

- 5 minutos;
- 10 minutos;
- 30 minutos.

Timeframe default: 15 segundos.

Para cada janela, o contrato calcula o número observado de candles, o número
esperado e o coverage ratio.

## Features calculadas

Para cada trade com símbolo, entrada válida e candles disponíveis:

- `entry_close`
- `entry_open`
- `entry_high`
- `entry_low`
- `entry_volume`
- `entry_return_1_candle`
- `sma_20`
- `dist_sma_20_pct`
- `rsi_14`
- `pre_entry_volatility_20`
- `lb_Nm_candle_count`
- `lb_Nm_expected_candle_count`
- `lb_Nm_coverage_ratio`
- `lb_Nm_ret_close`
- `lb_Nm_high_low_range_pct`
- `lb_Nm_volume_sum`

As features usam somente candles com timestamp menor ou igual à entrada. O PnL
não é usado como feature. Nenhum label é criado.

## Por que features não liberam operação

Coverage e features são insumos de pesquisa. Eles não aprovam live, canary,
alteração de risco, alteração de estratégia, promoção de modelo ou regra
candidata.

## Semântica blocked-by-default

Mesmo com features válidas, o payload continua `blocked`. A branch não concede
autoridade operacional.

## Safety flags

O payload preserva:

- `paper_only=true`
- `shadow_only=true`
- `read_only=true`
- `live_trading_enabled=false`
- `canary_release_allowed=false`
- `live_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `updates_freqtrade=false`
- `updates_risk_manager=false`
- `updates_qlib_runtime=false`
- `updates_ai_shadow_runtime=false`
- `writes_runtime=false`
- `writes_data=false`
- `writes_sqlite=false`
- `writes_parquet=false`
- `runs_training=false`
- `runs_ocr=false`
- `runs_ai_shadow_incremental=false`

## Próximos passos

- criar mistake/winner catalog em branch futura;
- criar pattern mining research em branch futura;
- criar candidate shadow rule registry em branch futura;
- criar OOS validation em branch futura;
- criar AI Shadow feedback bridge em branch futura.

## Ações proibidas

- alterar Freqtrade;
- alterar RiskManager;
- alterar Qlib runtime;
- alterar IA Shadow runtime;
- alterar modelos;
- alterar datasets;
- habilitar live;
- habilitar canary;
- enviar ordem real;
- usar exchange privada;
- escrever artefatos em `data/`, `runtime/`, `reports/`, `logs/` ou
  `freqtrade/`;
- usar features para liberar operação;
- promover regra candidata;
- promover modelo;
- criar regras candidatas nesta branch;
- usar PnL como feature.

## Execução

No-write por padrão:

```powershell
python .\scripts\build_daily_candle_coverage_entry_features_v1.py --project-root . --no-write --json
```

Escrita explícita somente para path fora de diretórios runtime:

```powershell
python .\scripts\build_daily_candle_coverage_entry_features_v1.py --project-root . --output "$env:TEMP\daily_candle_features.json" --json
```

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_daily_candle_coverage_entry_features_v1.py -q
python .\scripts\build_daily_candle_coverage_entry_features_v1.py --project-root . --no-write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
python -m pip_audit -r ".\requirements-dev.lock" --progress-spinner off
git diff --check
```
