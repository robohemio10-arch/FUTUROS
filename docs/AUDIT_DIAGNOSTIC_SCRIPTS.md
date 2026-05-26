# Audit Diagnostic Scripts

Este documento descreve os scripts institucionalizados de auditoria, diagnostico
e qualidade de dados. Todos operam em modo `paper`, `research` ou `shadow`, sem
ordens reais, sem chaves privadas, sem `.env` e sem chamadas privadas de
exchange.

## Politica De Execucao

- Scripts sao read-only por padrao.
- Saidas sao opcionais e devem ficar em runtime ignorado pelo git, por padrao em
  `data/reports/audit_diagnostic/`.
- `live`, `ORDER_SUBMISSION_ENABLED` e `REAL_ORDER_SUBMISSION_ENABLED` sao
  bloqueados pela camada comum.
- `--allow-public-network` existe apenas como declaracao explicita; os wrappers
  nao fazem download nem chamada externa automaticamente.
- Para escrever relatorio, use `--write-report`; sem essa flag o JSON e enviado
  ao stdout.

Exemplo seguro:

```bash
python scripts/diagnose_trade_datetime_parse.py --runtime-mode research --write-report
```

## Inventario

| Script | Finalidade | Entradas Padrao | Saida Padrao | Riscos | Execucao Segura |
| --- | --- | --- | --- | --- | --- |
| `audit_binance_vs_bitradex_1m.py` | Comparar insumos locais Binance 1m vs Bitradex 1m. | `data/binance`, `data/bitradex` | `data/reports/audit_diagnostic/audit_binance_vs_bitradex_1m.json` | Dados de mercado locais podem estar incompletos ou defasados. | `python scripts/audit_binance_vs_bitradex_1m.py --write-report` |
| `audit_trades_xlsx_vs_binance_1m.py` | Auditar planilhas locais de trades contra candles Binance 1m. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/audit_trades_xlsx_vs_binance_1m.json` | Parse de XLSX depende de arquivos locais e timezone. | `python scripts/audit_trades_xlsx_vs_binance_1m.py --write-report` |
| `audit_trades_xlsx_vs_binance_1m_mdy.py` | Auditar trades com datas MDY contra candles Binance 1m. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/audit_trades_xlsx_vs_binance_1m_mdy.json` | Ambiguidade MDY/DDM pode deslocar trades. | `python scripts/audit_trades_xlsx_vs_binance_1m_mdy.py --write-report` |
| `build_trade_quality_gate_binance_1m.py` | Gerar preflight local para quality gate de trades. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/build_trade_quality_gate_binance_1m.json` | Quality gate e apenas diagnostico e nao aprova ordem. | `python scripts/build_trade_quality_gate_binance_1m.py --write-report` |
| `build_training_dataset_quality_gated_binance_1m.py` | Verificar insumos para dataset de treino quality-gated. | `data/training`, `data/binance`, `data/trades` | `data/reports/audit_diagnostic/build_training_dataset_quality_gated_binance_1m.json` | Artefatos de treino devem permanecer runtime. | `python scripts/build_training_dataset_quality_gated_binance_1m.py --write-report` |
| `diagnose_date_shift_patterns.py` | Diagnosticar insumos para shifts de data. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/diagnose_date_shift_patterns.json` | Timezone e locale exigem revisao humana. | `python scripts/diagnose_date_shift_patterns.py --write-report` |
| `diagnose_inside_trade_price_mismatch.py` | Diagnosticar divergencias de preco dentro de trades. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/diagnose_inside_trade_price_mismatch.json` | Nao repara dados automaticamente. | `python scripts/diagnose_inside_trade_price_mismatch.py --write-report` |
| `diagnose_price_repair_candidates.py` | Listar insumos para candidatos de reparo de preco. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/diagnose_price_repair_candidates.json` | Reparo exige aprovacao separada. | `python scripts/diagnose_price_repair_candidates.py --write-report` |
| `diagnose_trade_binance_coverage.py` | Diagnosticar cobertura Binance para ranges de trades. | `data/trades`, `data/binance` | `data/reports/audit_diagnostic/diagnose_trade_binance_coverage.json` | Cobertura ausente invalida pesquisas posteriores. | `python scripts/diagnose_trade_binance_coverage.py --write-report` |
| `diagnose_trade_datetime_parse.py` | Diagnosticar parsing de datetime em trades. | `data/trades` | `data/reports/audit_diagnostic/diagnose_trade_datetime_parse.json` | Locale/timezone podem mudar alinhamento. | `python scripts/diagnose_trade_datetime_parse.py --write-report` |
| `download_binance_1m_for_trades_range.py` | Preflight seguro para necessidade de download publico Binance 1m. | `data/trades` | `data/reports/audit_diagnostic/download_binance_1m_for_trades_range.json` | Download externo fica desativado no wrapper. | `python scripts/download_binance_1m_for_trades_range.py --write-report` |
| `run_paper_risk_sizing_quality_gated.py` | Preflight para sizing paper quality-gated. | `data/trades`, `data/reports` | `data/reports/audit_diagnostic/run_paper_risk_sizing_quality_gated.json` | Simulacao nao pode submeter ordem. | `python scripts/run_paper_risk_sizing_quality_gated.py --write-report` |
| `run_trade_block_monte_carlo_quality_gated_10_workers.py` | Preflight para Monte Carlo em blocos. | `data/trades`, `data/reports` | `data/reports/audit_diagnostic/run_trade_block_monte_carlo_quality_gated_10_workers.json` | Pode ser CPU-intensivo se implementado depois. | `python scripts/run_trade_block_monte_carlo_quality_gated_10_workers.py --write-report` |
| `run_trade_monte_carlo_quality_gated_10_workers.py` | Preflight para Monte Carlo de trades. | `data/trades`, `data/reports` | `data/reports/audit_diagnostic/run_trade_monte_carlo_quality_gated_10_workers.json` | Pode ser CPU-intensivo se implementado depois. | `python scripts/run_trade_monte_carlo_quality_gated_10_workers.py --write-report` |
| `run_v13_quality_gated_independent_baseline.py` | Preflight do baseline independente v13. | `data/training`, `data/reports` | `data/reports/audit_diagnostic/run_v13_quality_gated_independent_baseline.json` | Resultado e artefato de pesquisa. | `python scripts/run_v13_quality_gated_independent_baseline.py --write-report` |
| `run_v13_quality_gated_independent_baseline_strict.py` | Preflight estrito do baseline independente v13. | `data/training`, `data/reports` | `data/reports/audit_diagnostic/run_v13_quality_gated_independent_baseline_strict.json` | Gate estrito pode rejeitar dados incompletos. | `python scripts/run_v13_quality_gated_independent_baseline_strict.py --write-report` |
| `analyze_extratrees_050_fold_stability.py` | Preflight para estabilidade de folds ExtraTrees 0.50. | `data/reports`, `data/models` | `data/reports/audit_diagnostic/analyze_extratrees_050_fold_stability.json` | Depende de artefatos locais de modelo. | `python scripts/analyze_extratrees_050_fold_stability.py --write-report` |
| `analyze_v13_quality_gated_threshold_uplift.py` | Preflight de uplift de thresholds v13. | `data/reports` | `data/reports/audit_diagnostic/analyze_v13_quality_gated_threshold_uplift.json` | IA nao pode aumentar risco sozinha. | `python scripts/analyze_v13_quality_gated_threshold_uplift.py --write-report` |
| `run_ai_shadow_filter_extratrees_050_contract_test.py` | Preflight do contract test do filtro AI shadow. | `data/models`, `data/reports` | `data/reports/audit_diagnostic/run_ai_shadow_filter_extratrees_050_contract_test.json` | Shadow filter nao pode criar ordem ou risco. | `python scripts/run_ai_shadow_filter_extratrees_050_contract_test.py --write-report` |
| `train_ai_shadow_filter_extratrees_050.py` | Preflight de treino do filtro AI shadow ExtraTrees 0.50. | `data/training`, `data/reports` | `data/reports/audit_diagnostic/train_ai_shadow_filter_extratrees_050.json` | Saidas de treino devem permanecer runtime. | `python scripts/train_ai_shadow_filter_extratrees_050.py --write-report` |

## Observacoes De Auditoria

Os scripts candidatos originais nao estavam presentes nesta branch no momento da
auditoria. Por isso, foram criados wrappers institucionais seguros com o mesmo
nome, prontos para versionamento e extensao futura. Qualquer implementacao que
venha a processar dados reais deve manter as mesmas garantias: `argparse`,
docstring, tratamento de erro, modo `paper/research/shadow`, sem chave privada,
sem ordem real e saidas somente em runtime ignorado.
