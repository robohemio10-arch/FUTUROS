# PAPER PROFIT PROTECTION — PATH-FAITHFUL WALK-FORWARD V1

## Objetivo

Validar, exclusivamente em research/read-only, os quatro candidatos de profit protection já descobertos na análise anterior, eliminando três fontes de falso edge:

1. uso de MFE futuro agregado;
2. ambiguidade intrabar de OHLC;
3. seleção do campeão usando o mesmo período posteriormente tratado como holdout.

Nenhum parâmetro novo de ROI, stoploss, risco, modelo ou runtime é aplicado por esta branch.

## Candidatos congelados

A branch não expande o espaço de busca. Avalia somente:

- `trigger_10bps__retain_75pct_mfe`;
- `trigger_10bps__net_breakeven`;
- `trigger_10bps__retain_50pct_mfe`;
- `trigger_25bps__retain_75pct_mfe`.

## Reconstrução causal do path

A simulação usa os candles intratrade retornados por `align_trades_to_candles()` e mantém um `running MFE` conhecido apenas até o candle anterior.

Para cada candle:

1. calcula o floor usando somente o pico favorável conhecido antes daquele candle;
2. verifica primeiro o extremo adverso;
3. somente se o stop não for atingido, atualiza o running MFE com o extremo favorável do candle atual.

Portanto, um novo high/low favorável não pode criar um trailing e executá-lo retrospectivamente dentro do mesmo candle.

## Ordem intrabar conservadora

Quando um candle contém simultaneamente um novo extremo favorável e cruzamento do floor anterior, a sequência assumida é:

`adverse first -> favorable second`

Isso reduz, e não aumenta, o benefício atribuído ao trailing.

## Candles de borda

High/low de um candle parcialmente anterior à entrada ou parcialmente posterior ao fechamento não é usado.

Só são considerados candles cujo intervalo completo está contido em:

`open_time_utc <= candle_interval <= close_time_utc`

Esse requisito evita usar extrema que pode ter ocorrido antes de o trade existir ou depois de já estar fechado.

## Gap through stop

Quando a abertura do candle já está além do floor, a execução simulada ocorre no `open` desfavorável, e não no preço teórico do floor.

## Custos

A simulação cobra:

- fees observadas positivas;
- funding positivo como custo;
- funding creditado é ignorado para manter conservadorismo;
- slippage de saída fixo em `10 bps`.

O slippage é contrato fixo desta validação e não participa de otimização.

## Timeframe

Preferência determinística:

1. `1m`;
2. `5m`.

O timeframe mais fino só é selecionado se possuir:

- pelo menos 50 trades elegíveis com path causal utilizável;
- cobertura causal mínima de 80%.

Caso contrário, o runner tenta o próximo timeframe.

## Walk-forward e holdout

Os trades elegíveis são ordenados cronologicamente.

- últimos 20%: holdout final intocado;
- primeiros 80%: development;
- development: três folds walk-forward com histórico expansivo.

O ranking dos quatro candidatos usa somente os validation folds do development.

O holdout não aparece em nenhuma chave de ranking e só é avaliado depois que um único campeão é congelado.

## Gate de congelamento pré-holdout

Um candidato só pode ser congelado quando:

- pelo menos 2 de 3 folds apresentam delta PnL positivo;
- PnL agregado das validações é positivo;
- expectancy agregada é positiva;
- profit factor é maior que 1 quando definido;
- delta agregado contra baseline é positivo;
- cobertura causal é pelo menos 80%.

## Gate do holdout final

O campeão congelado só passa a validação path-faithful quando, no holdout final:

- Net PnL > 0;
- expectancy > 0;
- profit factor > 1 quando definido;
- delta PnL contra o baseline > 0;
- maximum drawdown não supera o baseline;
- cobertura causal >= 80%.

`ready_for_paper_wiring=true` significa apenas que a evidência research passou esse gate. Não executa wiring, não altera Freqtrade e não concede autoridade operacional.

## Segurança

Flags permanentes nesta branch:

- `research_only=true`;
- `read_only=true`;
- `paper_only=true`;
- `operational_authority=false`;
- `sends_orders=false`;
- `exchange_private_access=false`;
- `changes_risk=false`;
- `changes_roi=false`;
- `changes_stoploss=false`;
- `writes_runtime=false`;
- `updates_freqtrade=false`;
- `deploy_performed=false`.

## CLI

```powershell
python scripts/run_paper_profit_protection_path_faithful_walkforward_v1.py `
  --project-root E:\FUTUROS `
  --allow-runtime-read `
  --json
```

A leitura de runtime, quando explicitamente habilitada, segue o mecanismo existente de cópia temporária/query-only do dataset de research. Nenhum artefato de runtime é escrito.
