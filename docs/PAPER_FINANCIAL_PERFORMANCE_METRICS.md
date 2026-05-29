# Paper Financial Performance Metrics

Esta branch cria a consolidação institucional das métricas financeiras reais do paper trading no SmartCrypto. A pergunta central é: o paper está gerando edge financeiro mensurável ou apenas ruído operacional?

## Fontes

O script procura fontes locais já materializadas, sem ler SQLite do Freqtrade diretamente:

```text
data/features/trade_enriched.parquet
data/features/training_dataset.parquet
data/features/training_dataset_quality_gated_binance_1m.parquet
```

A Fase 14 continua sendo a via segura para leitura de SQLite paper por snapshot. Este relatório consome apenas arquivos locais prontos e não altera fonte nenhuma.

## Métricas calculadas

O relatório global inclui:

- trades, wins, losses e win rate;
- PnL total;
- retorno médio, retorno mediano e expectancy;
- gross profit e gross loss;
- Profit Factor;
- Avg Win, Avg Loss e Payoff Ratio;
- Max Drawdown;
- Max Drawdown percentual quando houver coluna de equity/base;
- sequência máxima de wins e losses consecutivos.

Também são gerados resumos por símbolo/par, lado Long/Short, regime, estratégia, mês e dia quando as colunas correspondentes existirem.

## Win Rate vs Expectancy

Win rate mede quantas operações fecham positivas. Expectancy mede o resultado médio por trade. Uma estratégia pode ter win rate alto e expectancy ruim se as perdas forem grandes. Também pode ter win rate baixo e expectancy bom se os ganhos médios compensarem as perdas.

## Profit Factor

Profit Factor é `gross_profit / gross_loss`. Valores acima de 1 indicam lucro bruto maior que perda bruta. Quando não há perdas, o relatório não divide por zero: `profit_factor` fica `null` e `profit_factor_status` explica o caso, por exemplo `no_losses`.

## Max Drawdown

Max Drawdown é a maior queda da curva acumulada de PnL em relação ao pico anterior. Ele mostra a pressão financeira que a estratégia sofreu antes de recuperar ou encerrar a amostra. Sem equity/base, o drawdown é reportado na unidade da coluna de PnL/retorno detectada.

## Símbolo, lado e regime

Os resumos por símbolo e lado ajudam a separar edge real de concentração acidental. Se o resultado vem de um único par ou só de Long/Short, isso precisa ser tratado como risco. Regime e estratégia, quando disponíveis, ajudam a identificar onde o sistema performa ou falha.

## Critérios antes de live canary

O relatório expõe:

- `sample_size`
- `sample_warning`
- `minimum_recommended_trades`
- `metrics_reliable`

A recomendação mínima é não usar métricas como evidência institucional antes de 30 trades fechados. Abaixo disso, o relatório pode ficar `ok`, mas `metrics_reliable=false` e `sample_warning` explicam a limitação.

## Uso

```powershell
python .\scripts\run_paper_financial_performance_metrics.py
```

Com fonte explícita:

```powershell
python .\scripts\run_paper_financial_performance_metrics.py `
  --source data/features/trade_enriched.parquet `
  --report data/reports/paper_financial_performance_metrics_report.json `
  --minimum-recommended-trades 30
```

O script imprime JSON controlado no stdout e grava `data/reports/paper_financial_performance_metrics_report.json`.

## Bloqueios

O relatório retorna status controlado quando:

- não há fonte local disponível;
- não existe coluna de PnL/retorno;
- a coluna de PnL contém NaN, infinito ou valores não numéricos;
- o timestamp exigido não existe ou é inválido;
- o schema não pode ser processado.

## Garantias de segurança

- paper/shadow only;
- live trading desabilitado;
- submissão de ordem desabilitada;
- submissão real de ordem desabilitada;
- acesso privado à exchange desabilitado;
- não chama Phase13;
- não chama Freqtrade;
- não chama exchange;
- não escreve `active_freqtrade_signals.json`;
- não altera datasets;
- não altera SQLite.

## Limitações

O relatório é uma consolidação financeira, não uma prova causal. Ele depende da qualidade do feedback paper, da coluna de PnL escolhida e da sequência temporal disponível. Métricas com amostra pequena devem ser tratadas como diagnóstico, não autorização operacional.
