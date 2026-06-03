# Data Quality Report e Dataset Manifest

Esta branch adiciona duas camadas read-only para governança de dados do FUTUROS/SmartCrypto: um relatório de qualidade de datasets e um manifesto determinístico de artefatos. Ambas operam em modo paper/shadow only e não alteram datasets oficiais, SQLite do Freqtrade, modelos, registry, produtor de sinais ou configuração de runtime.

## Objetivo

O `data_quality_report` responde se os datasets usados por trades, enrichment, training, market features, microbatch, decisões e outcomes estão íntegros antes de qualquer etapa de treino, avaliação ou promoção. Ele detecta problemas como duplicidade por `order_id`, timestamps ausentes ou inválidos, preços ausentes, símbolos/lados inválidos, NaN/infinito em campos críticos, gaps temporais, linhas enriquecidas e não enriquecidas, candles de abertura/fechamento ausentes e exclusões documentadas.

O `dataset_manifest` cria uma impressão auditável de arquivos locais: tamanho, hash SHA-256, formato, linhas, colunas, schema hash e janela temporal. O manifesto é determinístico para permitir comparação entre execuções sem versionar os artefatos de runtime.

## Fontes

Os CLIs aceitam arquivos Parquet, CSV, JSON, JSONL e XLSX quando o ambiente tem suporte de leitura. Os caminhos típicos são:

- `data/trades/trades_master.xlsx`
- `data/features/trade_enriched.parquet`
- `data/features/training_dataset.parquet`
- `data/features/market_features_60d.parquet`
- `data/features/incremental_training_microbatch.parquet`
- arquivos de decisões e outcomes IA Shadow

## Relatórios

Por padrão, os outputs são runtime e não devem ser versionados:

- `data/reports/data_quality_report.json`
- `data/reports/dataset_manifest.json`

O relatório de qualidade retorna `ok`, `warning`, `blocked` ou `missing_input`. Em modo `--strict`, bloqueia inputs obrigatórios ausentes, dataset vazio, `order_id` duplicado, timestamps/preços críticos ausentes, símbolo/lado inválido, NaN/infinito em campos críticos e flags inseguras.

O manifesto retorna `ok`, `warning`, `blocked` ou `missing_input`. Em modo `--strict`, bloqueia arquivo ausente, vazio, schema ilegível, falha de hash e flags inseguras.

## Comandos

```powershell
python scripts/build_data_quality_report.py `
  --trades-master data/trades/trades_master.xlsx `
  --trade-enriched data/features/trade_enriched.parquet `
  --training-dataset data/features/training_dataset.parquet `
  --market-features data/features/market_features_60d.parquet `
  --microbatch data/features/incremental_training_microbatch.parquet `
  --strict
```

```powershell
python scripts/build_dataset_manifest.py `
  --inputs data/features/trade_enriched.parquet data/features/training_dataset.parquet `
  --dataset-role training_inputs `
  --timestamp-column timestamp `
  --strict
```

## Garantias de segurança

- `paper_only=true` e `shadow_only=true`.
- `live_trading_enabled=false`.
- `order_submission_enabled=false`.
- `real_order_submission_enabled=false`.
- `exchange_private_access=false`.
- `sends_orders=false`.
- `changes_risk_limits=false`.
- Nenhum arquivo oficial de dados é modificado.
- Nenhum acesso a exchange privada ou DB operacional do Freqtrade é feito.
- Nenhum arquivo em `data/`, `models/`, `reports/`, parquet, SQLite, CSV, XLSX, logs ou evidence deve ser versionado.
