# Qlib V2 Prospective Paper Confirmation V1

## Objetivo

Congelar o challenger econômico Qlib V2 **antes** de qualquer novo outcome e medir seu comportamento somente em trades Paper abertos depois da certificação pós-merge do PR #387.

Esta etapa existe para remover o viés de seleção da pesquisa histórica dos 905 trades. O resultado histórico positivo do V2 continua sendo evidência de pesquisa; não é tratado como lucro prospectivo certificado.

## Boundary canônico

A fronteira imutável desta V1 é:

```text
prospective_start_utc = 2026-09-08T19:38:11+00:00
certified_dev_commit = e2b7d8255b8efa503b547a0885d8422f73e26a52
certified_post_merge_ci_run_id = 34267098057
```

Somente trades com `open_time_utc > prospective_start_utc` entram na amostra prospectiva.

## Congelamento econômico

A policy é reconstruída deterministicamente usando apenas outcomes pré-boundary cujas labels estavam disponíveis antes de `prospective_start_utc - 300s`.

O contrato preserva integralmente o V2:

- target `absolute_stressed_net_pnl`;
- Qlib `LGBModel(loss="mse")`;
- stress adicional de 5 bps por padrão;
- todos os trades causalmente disponíveis no treino;
- somente longs elegíveis para seleção;
- quantis do threshold calculados apenas nos longs de calibração;
- nenhum outcome prospectivo em fit, early stopping ou calibração.

O `freeze_spec` grava o fingerprint do dataset pré-boundary, feature columns, threshold, quantil selecionado, configuração/model metadata e `policy_sha256`. Uma execução futura bloqueia se qualquer campo congelado divergir.

## Avaliação prospectiva

Closed outcomes pós-boundary são alinhados novamente às features do último candle 5m completamente fechado antes da entrada. A decisão que a policy congelada teria tomado é reconstruída sem usar o PnL posterior como predictor.

Cada observação registra `trade_id`, símbolo, side, open/close time, timestamp PIT, score, threshold, elegibilidade, seleção, PnL realizado/stressed e fingerprints da policy/dataset.

O relatório apresenta baseline prospectivo, tratamento selecionado, delta de Net PnL stressed, expectancy e Profit Factor. **Não existe gate de promoção nesta V1.** O contrato V2 não pré-registrou tamanho mínimo de amostra nem duração mínima para adjudicar lucro prospectivo; inventar esses valores depois de ver os outcomes criaria novo viés.

## Execução

Inspeção read-only:

```powershell
python .\scripts\build_qlib_v2_prospective_paper_confirmation_v1.py --project-root . --json
```

Primeiro congelamento controlado:

```powershell
python .\scripts\build_qlib_v2_prospective_paper_confirmation_v1.py `
  --project-root . `
  --write-freeze-spec `
  --write-report `
  --json
```

Execuções posteriores reutilizam automaticamente o freeze spec existente e falham se o fingerprint pré-boundary ou o threshold divergir.

## Invariantes

```text
paper_only=true
shadow_only=true
research_only=true
operational_authority=false
promotion_allowed=false
model_promotion_performed=false
active_model_changed=false
changes_risk=false
sends_orders=false
exchange_private_access=false
writes_runtime=false
prospective_labels_used_for_training=false
prospective_labels_used_for_calibration=false
threshold_recalibration_after_boundary_allowed=false
```

A escrita opcional é limitada a evidência de research em `data/reports/qlib_v2/`; não existe escrita em runtime, Freqtrade, RiskManager, registry ativo, modelo ativo ou exchange.
