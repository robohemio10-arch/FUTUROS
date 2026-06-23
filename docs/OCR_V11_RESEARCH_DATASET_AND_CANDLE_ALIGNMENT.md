# OCR V1.1 Research Dataset and Candle Alignment

## Objetivo

Esta etapa cria uma base de pesquisa read-only a partir do master OCR V1.1 e de
candles públicos históricos. O resultado serve para análise posterior de
qualidade, contexto de entrada, MFE, MAE e contrafactual de lado. Ele não é um
dataset oficial de treino e não altera o pipeline operacional.

O fluxo permanece estritamente `paper/shadow only`: não executa OCR, não importa
trades, não promove quality-gated, não treina IA Shadow, não limpa SQLite, não
altera risco/modelo e não acessa exchange privada ou envio de ordens.

## Fontes e autoridade

- `data/trades/trades_master.xlsx`: autoridade OCR V1.1 para identidade, hash e
  contagem de linhas.
- `data/trades/trades_master.parquet`: projeção canônica opcional. Ela só é
  usada quando possui as colunas esperadas e a mesma quantidade de linhas da
  autoridade XLSX.
- `data/features/market_features_60d.parquet`: primeira fonte de candles 1m.
- `data/raw/futures_ohlcv_60d.parquet`: fallback de candles 1m.

O hash SHA256 esperado do master de produção e a contagem esperada de 3058
linhas são evidências auditáveis. Uma divergência gera warning; o script não
reescreve nem corrige o master.

## Execução

O modo padrão é no-write e processa tudo em memória:

```powershell
python .\scripts\build_ocr_v11_research_dataset.py --no-write --json
```

Para materializar somente artefatos de pesquisa runtime:

```powershell
python .\scripts\build_ocr_v11_research_dataset.py --write --json
```

Os caminhos podem ser substituídos por `--master`, `--master-projection`,
`--candles`, `--output`, `--report` e `--executive-reports-dir`.

## Saídas runtime

O modo `--write` gera atomicamente:

- `data/research/ocr_v11_trade_research_dataset.parquet`
- `data/reports/ocr_v11_research_dataset_audit.json`
- `data/reports/training_reports/ocr_v11_research_dataset_summary.json`
- `data/reports/training_reports/ocr_v11_research_dataset_executive.md`

Esses caminhos ficam sob `data/`, são artefatos runtime e não devem ser
versionados.

## Contrato temporal e anti-lookahead

O candle de entrada é o candle 1m cujo timestamp corresponde ao minuto de
abertura do trade. O contexto de decisão, porém, usa somente o candle mais
recente que estava completamente encerrado no instante da abertura:

`entry_feature_timestamp + 1 minuto <= open_time`

Assim, OHLCV, retornos, volatilidades, EMAs, RSI, tendência e regime em
`entry_*` são point-in-time. Colunas `future_ret_*` não são usadas.

MFE, MAE, tempo até MFE/MAE e o contrafactual de lado oposto são métricas de
outcome calculadas apenas dentro do intervalo real entre abertura e fechamento.
Elas não são features de decisão e não autorizam inferência ou promoção de
modelo.

## Qualidade e elegibilidade

Cada linha registra validações de símbolo, lado, timestamps, preços e PnL, além
de presença dos candles de entrada/saída e continuidade do intervalo. Uma linha
só recebe `is_research_eligible=true` quando os campos básicos são válidos, o
alinhamento está completo e existe contexto point-in-time anterior à decisão.

Falhas permanecem explícitas em `research_block_reason`; dados ausentes não são
inventados. Candles com timestamp inválido, volume negativo ou preços fora dos
limites institucionais de BTCUSDT/ETHUSDT são descartados e contabilizados.

## Relatório executivo

`smartcrypto/research/reporting.py` contém funções puras para:

- tabelas por símbolo, lado, alinhamento e elegibilidade;
- resumo da qualidade do alinhamento;
- dados tabulares prontos para gráficos futuros;
- resumo executivo JSON e Markdown.

As funções não escrevem arquivos e não dependem de PDF ou biblioteca de
visualização. O Markdown contém data, fonte, quantidade total/elegível/bloqueada,
símbolos, período, qualidade do candle alignment e conclusão curta.

## Recursos

As evidências registram os limites configurados, sem paralelismo implícito:

- `SMARTCRYPTO_TRAINING_WORKERS` (default `10`)
- `SMARTCRYPTO_TRAINING_MAX_RAM_GB` (default `16`)

## Limitações

- A cobertura depende do intervalo real disponível nos candles locais.
- Trades fora dessa janela permanecem bloqueados, sem extrapolação.
- O contrafactual usa o mesmo movimento de preço, volume fechado e custo de
  taxas observado; não modela slippage ou dinâmica de execução alternativa.
- Esta branch não treina, avalia nem promove modelos.

## Validação

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_ocr_v11_research_dataset.py -q
python .\scripts\build_ocr_v11_research_dataset.py --no-write --json
python .\scripts\build_ocr_v11_research_dataset.py --write --json
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root "." --json
python -m pip_audit -r .\requirements-dev.lock --progress-spinner off
```
