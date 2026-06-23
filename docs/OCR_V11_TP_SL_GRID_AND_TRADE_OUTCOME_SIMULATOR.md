# OCR V1.1 TP/SL Grid and Trade Outcome Simulator

## Objetivo

Esta camada pesquisa outcomes alternativos para os trades da Master OCR V1.1.
Ela compara grades de take profit, stop loss e trailing stop sem alterar trades,
datasets oficiais, modelos, risco, Qlib runtime ou Freqtrade.

O simulador é estritamente `paper/shadow only`. Nenhuma estratégia ranqueada é
promovida e nenhuma simulação autoriza live, canary ou envio de ordens.

## Fontes

- `data/research/ocr_v11_trade_research_dataset.parquet`
- `data/features/market_features_60d.parquet`
- fallback de candles: `data/raw/futures_ohlcv_60d.parquet`

O research dataset da Branch 01 é obrigatório. Se estiver ausente, a execução
retorna `status=blocked` e `reason=missing_research_dataset`; o simulador não
reconstrói nem modifica a Master automaticamente.

## Execução

O default é no-write:

```powershell
python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --no-write --json
```

Para materializar exclusivamente outputs de pesquisa:

```powershell
python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --write --json
```

O CLI aceita caminhos customizados, grades `--tp-bps` e `--sl-bps`,
`--atr-multipliers`, `--trailing-atr-multipliers`, custos, workers e limite de
RAM. Listas são informadas como valores separados por espaço.

## Estratégias simuladas

1. TP e SL fixos em bps, usando o produto cartesiano das grades configuradas.
2. TP e SL em múltiplos do ATR disponível antes da entrada.
3. Trailing stop em múltiplos do ATR, sem TP fixo.

O ATR usa o valor `atr_14` válido da fonte ou uma média móvel de true range de
14 candles. Em ambos os casos, o valor selecionado pertence ao último candle
completamente encerrado antes ou no instante de abertura.

## Contrato temporal

Somente candles completamente contidos entre `open_time` e `close_time` entram
na trajetória simulada. Candles parciais de abertura ou fechamento são
excluídos porque seu OHLC contém movimentos fora da vida observável do trade.
Isso evita atribuir ao trade extremos ocorridos antes da entrada ou após a
saída.

Trades sem candles completos, sem volume válido ou sem ATR quando a estratégia
o exige permanecem `simulation_status=blocked`. Candles não são inventados nem
extrapolados.

## Regras conservadoras

- Quando TP e SL aparecem no mesmo candle, SL é aplicado primeiro.
- Fee e slippage são calculados sobre o turnover de entrada e saída.
- Long e short usam fórmulas direcionais separadas.
- Trailing stop é atualizado pelo extremo favorável e, diante de ambiguidade
  intrabar, o toque no stop atualizado é considerado.
- Se nenhum limite é tocado, a saída usa preço e horário originais.
- MFE, MAE e extremos são outcomes pós-trade; nunca são features de entrada.

## Outcomes por trade

O arquivo por trade contém o resultado da estratégia mais bem ranqueada,
incluindo hits, ordem dos hits, custos, PnL bruto/líquido, delta contra o PnL
original e simulação do lado oposto. Também registra:

- hold até a saída original;
- saída no extremo favorável observado;
- saída no extremo adverso observado;
- MFE/MAE e tempos correspondentes da Branch 01.

Esses contrafactuais são descritivos e não representam fills executáveis.

## Métricas e ranking

Cada estratégia agrega PnL líquido, gross profit/loss, Profit Factor, win/loss
rate, payoff, expectancy, mediana, drawdown, perdas consecutivas e recovery
factor. A ordem dos trades é temporal e os empates usam `strategy_id`, mantendo
determinismo.

O ranking usa percentis determinísticos:

```text
ranking_score =
  normalized_net_pnl
  + normalized_profit_factor
  + normalized_expectancy
  - normalized_max_drawdown_penalty
  - instability_penalty
```

`is_candidate_best` marca exatamente uma linha para análise. Não existe promoção
automática, alteração de registry ou atualização de estratégia operacional.

## Saídas runtime

Somente `--write` gera:

- `data/research/ocr_v11_tp_sl_grid_results.parquet`
- `data/research/ocr_v11_trade_outcome_simulation.parquet`
- `data/reports/ocr_v11_tp_sl_grid_summary.json`
- `data/reports/training_reports/ocr_v11_tp_sl_executive.md`
- `data/reports/training_reports/ocr_v11_tp_sl_summary.json`

Todos são ignorados pelo Git. As escritas usam substituição atômica.

## Relatório executivo e gráficos futuros

`smartcrypto/research/reporting.py` prepara, sem renderizar PNG ou PDF:

- top 10 por ranking;
- top 10 por PnL líquido;
- top 10 por menor drawdown;
- matriz TP/SL fixa para heatmap;
- distribuição de PnL simulado;
- comparação original contra a melhor simulação.

O Markdown executivo registra base, cobertura, melhor candidato, comparação com
o original, risco máximo, regra SL-first, conclusão e próxima ação recomendada.

## Limitações

- Candles 1m não revelam a ordem intrabar; por isso o resultado é conservador.
- Fills reais podem sofrer gap, latência e slippage diferente do valor fixado.
- O ranking é exploratório e precisa de validação fora da amostra.
- Estratégias ATR ficam bloqueadas quando não há ATR point-in-time válido.

## Segurança

O relatório preserva flags explícitas de paper/shadow e declara como `false`
live, ordens, exchange privada, alterações de risco/modelo, OCR, treino,
quality-gated, SQLite, Freqtrade e Qlib runtime.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_ocr_v11_tp_sl_grid_simulator.py -q
python -m pytest .\tests\test_ocr_v11_research_dataset.py -q
python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --no-write --json
python .\scripts\run_ocr_v11_tp_sl_grid_simulator.py --project-root . --write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```
