# MARKET FEATURES CONTINUITY + ECONOMIC CHALLENGER V1

## Objetivo

Eliminar regressões de continuidade causadas por recomputação de indicadores sem warm-up suficiente e abrir um challenger econômico Qlib nativo que use contexto de mercado point-in-time, sem alterar runtime operacional, risco, registry, sinais, ordens ou acesso privado à exchange.

## Problema corrigido

O refresh anterior calculava features apenas sobre a janela recente e depois fazia `concat + drop_duplicates(..., keep="last")`. As primeiras linhas da janela recente ainda estavam em warm-up para EMA200, volatilidade e demais indicadores. Essas linhas com `NaN` podiam sobrescrever linhas históricas anteriormente completas e deixar cicatrizes permanentes no artefato `market_features_60d.parquet`.

## Refatoração de continuidade

`smartcrypto/data/feature_builder.py` passa a oferecer um builder puro em memória, preservando o writer central de no-lookahead.

`smartcrypto/qlib_engine/market_features_refresh.py` passa a:

1. combinar OHLCV do artefato existente, fonte raw configurada e candles públicos recentes;
2. auditar regressões pós-warm-up em features estáveis;
3. executar `full_continuity_repair` quando o artefato atual contém cicatrizes;
4. recalcular todo o histórico raw disponível de cada `symbol/timeframe` afetado pelo refresh, eliminando `EMA restart drift`;
5. preservar grupos não afetados sem recomputação desnecessária;
6. preferir valores recalculados não nulos e nunca substituir valor existente por `NaN` de warm-up;
7. bloquear a publicação se persistir regressão de continuidade;
8. validar freshness individualmente para cada grupo esperado (por exemplo BTCUSDT/5m e ETHUSDT/5m), sem aceitar um `max(ts)` global como prova de que todos os grupos estão atualizados;
9. manter o contrato central de remoção de qualquer `future_ret_*` do artefato operacional.

## Challenger econômico Qlib market-context

Novo módulo:

`smartcrypto/learning/paper_autolearning/qlib_market_context_economic_challenger.py`

CLI:

`python scripts/build_qlib_market_context_economic_challenger_v1.py --project-root . --outcome-path data/feedback/outcome_events.parquet --market-features-path data/features/market_features_60d.parquet --json`

### Modelo

- Microsoft Qlib `qlib.contrib.model.gbdt.LGBModel`;
- LightGBM regression via Qlib;
- target: `stressed_net_pnl_per_notional_bps`;
- sem fallback de produção;
- hiperparâmetros conservadores para amostra pequena;
- split interno de validação exclusivamente dentro da janela de fit causal para early stopping;
- labels da calibração econômica nunca entram no fit nem no early stopping do LightGBM;
- MLflow/Qlib somente em diretório temporário local e offline.

### Features permitidas

Somente campos disponíveis antes da entrada:

- side e símbolo;
- retornos 5m de 1/3/5/10/15/30 candles;
- distância de EMA20/50/200;
- RSI normalizado;
- MACD normalizado pelo preço;
- ATR percentual;
- volatilidade 30/120;
- volume relativo e z-score;
- range/body/wicks;
- trend score e dummies de regime;
- interações side × trend/returns/MACD;
- hora do dia e dia da semana em codificação cíclica.

São explicitamente excluídos como features preditivas:

- `net_pnl`;
- `profit_ratio`;
- fees/funding;
- `notional`;
- `quantity`;
- `leverage`;
- close time e qualquer campo posterior à entrada;
- qualquer `future_ret_*`.

## Anti-leakage reforçado

O candle 5m só fica disponível em `candle_ts + 5 minutos` e precisa satisfazer `available_at <= trade_open`.

O label de um trade só pode entrar no fit se seu `close_time` for anterior ao ponto de informação da calibração/teste, com embargo adicional de 300 segundos.

A calibração do threshold usa apenas outcomes passados. O threshold congelado é então aplicado ao próximo fold forward.

## Gate econômico

O challenger continua usando o gate canônico de robustez econômica, com foco em:

- stressed Net PnL positivo;
- expectancy positiva;
- Profit Factor mínimo 1.10;
- delta stressed Net PnL positivo versus executar todos os trades;
- uplift de expectancy;
- uplift de eficiência por capital-hora;
- drawdown controlado;
- pelo menos 2 de 3 folds positivos.

Accuracy e ROC AUC não concedem autoridade econômica.

## Segurança operacional

Permanece invariável:

- `paper_only=true`;
- `shadow_only=true`;
- `research_only=true`;
- `operational_authority=false`;
- `promotion_allowed=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `changes_risk=false`;
- nenhuma escrita em runtime/model registry/SQLite/parquet pelo challenger.

## Validação local desta entrega

No ambiente de construção sem `pyarrow`:

- `python -m compileall smartcrypto scripts tests`: OK;
- testes direcionados de continuidade + challenger: `13 passed`;
- 2 testes de integração parquet ficam `skipped` localmente e devem executar no CI institucional, onde `pyarrow` faz parte do grafo Qlib/security-clean;
- há teste determinístico que compara o rebuild de grupo afetado contra o cálculo de histórico completo, garantindo igualdade exata dos indicadores recursivos e eliminando restart drift;
- há teste de freshness por grupo que bloqueia o refresh quando um símbolo está stale mesmo que outro mantenha o `max(ts)` global recente.

A promoção econômica continua bloqueada até o CLI nativo Qlib ser executado contra os artefatos reais e o gate completo passar.
