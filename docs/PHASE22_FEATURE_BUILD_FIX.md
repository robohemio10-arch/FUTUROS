# Phase 22 Feature Build Fix

## Contexto

O build historico de features da Fase 22 falhava em
`scripts/build_phase22_market_features.py` com:

```text
TypeError: arg must be a list, tuple, 1-d array, or Series
```

A causa provavel era a presenca de colunas duplicadas nos candles brutos. Em
Pandas, `raw[col]` retorna um `DataFrame` quando `col` aparece mais de uma vez,
e `pd.to_numeric` exige entrada 1D.

## Correcao

- Colunas duplicadas agora sao colapsadas explicitamente antes da normalizacao.
- Selecoes de coluna passam por `series_1d`, garantindo `Series` antes de
  `pd.to_numeric`.
- A normalizacao aceita aliases de timestamp: `timestamp`, `ts`, `ts_ms` e
  `open_time`.
- Colunas minimas sao validadas: timestamp, `symbol`, `open`, `high`, `low`,
  `close` e `volume`.
- Erros de corrupcao de dados usam `Phase22FeatureBuildError` com mensagens
  claras.
- Cada arquivo bruto e normalizado isoladamente antes da concatenacao de
  multiplos simbolos.

## Modo Seguro

O script continua sem live trading, sem chaves, sem `.env`, sem chamada privada
de exchange e sem envio de ordem. Testes devem usar paths temporarios e nao
devem sobrescrever arquivos reais em `data/`.

Comando recomendado:

```bash
python -m pytest tests/test_phase22_feature_build.py
```
