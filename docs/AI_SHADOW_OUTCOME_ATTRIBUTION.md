# AI Shadow Outcome Attribution

## Objetivo

Adicionar attribution financeiro read-only para a IA Shadow do FUTUROS/SmartCrypto.

O relatório responde:

- AI_ACCEPT teve melhor retorno?
- AI_REJECT evitou prejuízo?
- SHADOW_ENTRY/SHADOW_SKIP têm edge mensurável?
- Qual faixa de probabilidade tem melhor expectancy?
- Qual threshold maximiza expectancy?
- Qual threshold maximiza Profit Factor?

## Fontes padrão

- data/features/training_dataset_quality_gated_binance_1m.parquet
- data/runtime/ai_shadow_filter_decisions.sqlite

## Saída padrão

- data/reports/ai_shadow_outcome_attribution_report.json

## Comando

python .\scripts\run_ai_shadow_outcome_attribution.py

## Status possíveis

- ok: attribution calculada
- blocked: bloqueio lógico, como alinhamento estrito divergente
- missing_dataset: dataset não encontrado
- missing_decisions: SQLite de decisões ausente ou vazio
- missing_join_key: coluna trade_id ausente
- missing_probability_column: nenhuma coluna de probabilidade encontrada
- missing_outcome_column: nenhuma coluna de resultado financeiro encontrada
- invalid_schema: schema inválido, dados vazios ou valores não numéricos

## Métricas

O relatório calcula:

- overall_metrics
- metrics_by_decision
- probability_bucket_summary
- threshold_summary
- best_threshold_by_expectancy
- best_threshold_by_profit_factor
- symbol_summary
- side_summary

## Segurança

Este fluxo é estritamente analítico.

Não envia ordem, não acessa exchange privada, não chama Freqtrade, não chama Phase13, não escreve active_freqtrade_signals.json, não altera datasets de origem e não altera SQLite de origem.

Campos fixos de segurança no JSON:

- runtime_mode: shadow
- shadow_only: true
- live_trading_enabled: false
- order_submission_enabled: false
- real_order_submission_enabled: false
- exchange_private_access: false

## Limitações

O relatório não prova edge live. Ele mede outcome histórico/paper/shadow disponível.

Antes de qualquer decisão operacional, ainda são necessários validação fora da amostra, auditoria anti-leakage, custos, spread, slippage, Monte Carlo, paper/shadow soak prolongado e gates de risco.
