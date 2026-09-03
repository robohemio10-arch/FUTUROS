# Market Features Rematerialization Research V2

## Objetivo

Esta frente implementa a lacuna independente identificada após P07: rematerialização
point-in-time de features de mercado em candles de 5 minutos, com diagnóstico de drift e
um único challenger `scikit-learn` efêmero opcional.

Ela é deliberadamente independente do Qlib/P08 e não possui autoridade operacional.

## Contrato point-in-time

A semântica de timestamp é `candle_open`. Uma barra só existe para uma decisão após o
fechamento completo da barra:

```text
available_at_utc = candle_timestamp_utc + 5 minutos
available_at_utc <= trade_open_time_utc
0 <= feature_age_seconds < 300
```

Consequências obrigatórias:

- o candle corrente não pode ser usado antes de fechar;
- gaps de 5 minutos quebram o segmento de rolling;
- não existe forward-fill através de gaps;
- não existe imputação de feature ausente;
- linhas sem feature completa ficam `blocked`.

## Features V2

A primeira versão contém somente features determinísticas pre-entry:

```text
feature_5m_ret_1
feature_5m_ret_3
feature_5m_range_pct
feature_5m_body_pct
feature_5m_volume_rel_12
feature_5m_ema_gap_12
```

O V2 reutiliza `smartcrypto.learning.feature_contracts.build_feature_contract` para
classificar feature/label/outcome/metadata e bloquear leakage. `net_pnl` é usado apenas
para derivar `label_profitable` quando existe; nunca entra na matriz de features.

## Lineage e drift

O report publica hashes determinísticos de:

- trades normalizados;
- candles 5m rematerializados;
- linhas point-in-time prontas.

O diagnóstico de drift compara cronologicamente a metade inicial e a metade final das
linhas prontas e publica média, desvio-padrão de referência e delta médio padronizado por
feature. Outcome/PnL não é usado como feature no drift.

## Challenger efêmero

`--run-challenger` habilita somente um smoke institucional:

- `LogisticRegression` com `StandardScaler`;
- split temporal 75/25;
- embargo de 300 segundos;
- mínimo de 40 linhas rotuladas;
- diversidade das duas classes obrigatória em treino e teste;
- comparação contra baseline de classe majoritária;
- nenhum `.joblib`, pickle ou artefato de modelo é escrito;
- nenhum registry é alterado;
- nenhum candidato recebe elegibilidade de promoção.

Bloqueio do challenger por amostra/classe insuficiente produz `warning` quando a
rematerialização point-in-time em si está válida.

## CLI

O CLI é no-write por default, mas **não assume nenhum dataset de trades legado**.
`--trades-path` é obrigatório e deve apontar explicitamente para uma fonte read-only
autorizada para a pesquisa atual.

```powershell
python scripts/run_market_features_rematerialization_research_v2.py `
  --project-root . `
  --trades-path <research_trade_dataset> `
  --candles-path data/features/market_features_60d.parquet `
  --json
```

Challenger efêmero opcional:

```powershell
python scripts/run_market_features_rematerialization_research_v2.py `
  --project-root . `
  --trades-path <research_trade_dataset> `
  --candles-path data/features/market_features_60d.parquet `
  --run-challenger `
  --json
```

`--write-report` pode gravar somente JSON sob `data/reports`. Não há escrita de parquet,
SQLite, runtime, sinal, registry ou modelo.

## Não objetivos

Esta branch não implementa:

- Qlib training ou resolução do gate de segurança Qlib;
- P08;
- paper holdout selection;
- Monte Carlo;
- ranking/promote de candidatos;
- Freqtrade strategy changes;
- RiskManager changes;
- execução ou ordens;
- acesso a exchange privada.

## Safety invariants

```text
QLIB_SECURITY_GATE_REMAINS_BLOCKED=true
QLIB_SECURITY_GATE_BYPASSED=false
P08_ALLOWED=false
research_only=true
paper_only=true
shadow_only=true
operational_authority=false
live_release_allowed=false
canary_release_allowed=false
model_promotion_performed=false
active_model_changed=false
writes_runtime=false
sends_orders=false
changes_risk=false
exchange_private_access=false
```
