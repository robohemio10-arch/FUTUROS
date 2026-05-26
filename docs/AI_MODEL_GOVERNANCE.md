# AI Model Governance

Esta camada adiciona governanca institucional para modelos em modo
`research`/`shadow`. Ela nao executa trades, nao chama exchange, nao usa chaves,
nao altera risco diretamente e nao promove modelos para live.

## Componentes

- `FeatureContract`: valida nomes, ordem, presenca, tipos numericos, NaN,
  infinito, ranges opcionais e versao do contrato antes de treino/scoring.
- `ModelRegistry`: registra metadados de modelos locais com status
  `CANDIDATE`, `APPROVED_FOR_SHADOW`, `REJECTED` ou `ROLLED_BACK`.
- `DriftMonitor`: calcula PSI simples por feature e retorna `OK`, `WARNING` ou
  `BLOCKED`. O bloqueio e interpretado como `BLOCK_AI`, nao como bloqueio total
  do bot.
- `ModelDecisionLogger`: grava decisoes de modelo em JSONL append-only, sem
  segredos no payload.
- `OutcomeTracker`: associa resultados posteriores a `decision_id` e calcula
  metricas simples.

## Politica De Seguranca

- `LIVE_ENABLED=false`
- `ORDER_SUBMISSION_ENABLED=false`
- `REAL_ORDER_SUBMISSION_ENABLED=false`
- Sem ordem real.
- Sem leitura de conta privada.
- Sem side effects em import.
- Escritas devem usar paths runtime ignorados, como `data/runtime/`.

## Validacao Recomendada

```bash
python -m compileall smartcrypto tests
python -m pytest tests/test_feature_contract.py tests/test_model_registry.py tests/test_drift_monitor.py tests/test_model_decision_logger.py tests/test_outcome_tracker.py
```
