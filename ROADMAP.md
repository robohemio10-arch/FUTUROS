# Roadmap

## Fase 1 — Fundação de Dados

- Consolidar trades do OCR
- Normalizar timestamps em UTC
- Baixar dados Binance Futures 1m e 5m
- Criar `market_features`
- Criar SQLite com índices

## Fase 2 — Enriquecimento

- Criar `trade_enriched`
- Calcular duração, MFE, MAE, retorno e drawdown
- Criar labels supervisionados

## Fase 3 — Modelo

- Treinar baseline
- Validar walk-forward
- Comparar contra regras simples
- Exportar scores auditáveis

## Fase 4 — Paper com Freqtrade

- Gerar `freqtrade_signals.json`
- Strategy lê sinal externo
- RiskManager bloqueia sinais inválidos
- Logs de decisão são persistidos

## Fase 5 — Operação Controlada

- Dashboard
- Kill switch
- Métricas
- Evidências
- Soak test
- Go/No-Go formal

## Live

Live real permanece bloqueado até haver paper/soak com evidência, reconciliação, kill switch validado, executor único e limites de risco aprovados.
