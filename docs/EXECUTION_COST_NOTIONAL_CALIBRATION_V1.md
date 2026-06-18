# EXECUTION COST NOTIONAL CALIBRATION V1

## Objetivo

Corrigir a calibração research-only de custos de execução para a validação histórica 15s, preservando invariantes paper/shadow.

## Causa raiz

A implementação anterior tinha dois problemas independentes:

1. O parser numérico removia todos os pontos antes de converter vírgula decimal. Isso corrompia preços dot-decimal já normalizados, por exemplo `60391.6488785` virava `603916488785`.
2. A inferência `entry_price * volume_posicao` aceitava outliers OCR sem saneamento. Na base real foram observados preços com escala decimal deslocada (`677701` em vez de `67770.1`, `217777` em vez de `2177.77`) e volumes de posição corrompidos (`volume_posicao=34.0` enquanto `volume_fechado=0.14445`).

Esses dois efeitos inflavam o notional por múltiplas ordens de grandeza e geravam PnL after-costs artificialmente bilionário.

## Correção

- Parser numérico robusto para OCR/CSV misto.
- Normalização simbólica de preço para BTC/ETH com correção decimal por escala de 10 quando o preço está fora de faixa institucional.
- Fallback row-level de tamanho: se `volume_posicao` produz notional acima do cap de sanidade, o motor tenta `volume_fechado`, `volume_transacao` e demais candidatos equivalentes.
- Cap de sanidade research-only por trade: `max_trade_notional_usdt=100000.0`.
- Proveniência explícita do notional no relatório.
- Contadores de qualidade: `notional_price_adjusted_rows`, `notional_size_fallback_rows`, `notional_invalid_rows`.
- Percentis de notional adicionados ao relatório.

## Safety

- `paper_only=true`
- `shadow_only=true`
- `runtime_mode=paper`
- `live_trading_enabled=false`
- `live_release_allowed=false`
- `canary_release_allowed=false`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`
- `sends_orders=false`
- `changes_risk=false`
- `changes_model=false`
- `changes_training_dataset=false`

## Validação

```powershell
python -m compileall -q smartcrypto tests scripts
python -m pytest tests\test_full_historical_validation_15s_core_v1.py -q

python scripts\run_full_historical_validation_15s.py `
  --project-root . `
  --from-date 2026-01-05 `
  --timeframe 15s `
  --json `
  --no-write

python scripts\generate_project_manifest.py
python scripts\generate_project_manifest.py --check
python scripts\scan_versioned_secrets.py --json
git diff --check
git status -sb
```

## Limite de uso

Este módulo continua sendo evidência de pesquisa. Ele não libera readiness, canary ou live trading. Qualquer leitura do resultado after-costs deve ser tratada como simulação determinística de custos, não como autorização operacional.
