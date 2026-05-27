# AI Shadow Dashboard Panel

O painel AI Shadow adiciona uma visualizacao read-only das decisoes geradas pelo AI Shadow Entry Observer. Ele nao executa trade, nao chama exchange privada, nao altera configuracao e nao escreve arquivos runtime.

## Arquivos lidos

O painel le apenas arquivos locais de runtime, quando existirem:

- `data/reports/ai_shadow_entry_observer_report.json`
- `data/reports/ai_shadow_entry_decisions.jsonl`

Se os arquivos nao existirem, a tela mostra um estado vazio amigavel e o comando recomendado para gerar os relatorios.

## Como gerar o relatorio do observador

```powershell
python scripts/run_ai_shadow_entry_observer.py `
  --features data/features/training_dataset_open_decision_clean.parquet `
  --model-report data/reports/model_vs_baseline_financial_evaluation_report.json `
  --output data/reports/ai_shadow_entry_observer_report.json `
  --decisions-output data/reports/ai_shadow_entry_decisions.jsonl `
  --id-column trade_id `
  --symbol-column symbol `
  --time-column open_1m_ts `
  --target-column target_win `
  --probability-threshold 0.60 `
  --max-rows 500 `
  --dry-run true `
  --shadow-only true `
  --seed 42
```

Esses outputs continuam ignorados pelo Git e nao devem ser versionados.

## O que o painel mostra

- status do observador;
- `rows_observed`;
- `shadow_entry_count`;
- `shadow_skip_count`;
- `blocked_count`;
- threshold de probabilidade;
- `model_name`, `model_version` e `model_source`;
- `leakage_status`;
- `safety_status`;
- ultimas decisoes do JSONL em tabela.

A tabela de decisoes mostra:

- `created_at`
- `symbol`
- `open_1m_ts`
- `probability_win`
- `probability_threshold`
- `decision`
- `decision_reason`
- `model_name`
- `blocked_reason`

## Interpretacao

- `SHADOW_ENTRY`: o observador teria marcado uma entrada shadow porque `probability_win >= probability_threshold`.
- `SHADOW_SKIP`: o observador decidiu nao marcar entrada shadow.
- `BLOCKED`: a decisao ou o relatorio foi bloqueado por safety, leakage ou falta de dados.

## Safety

O painel destaca explicitamente:

- `shadow_only=true`
- `dry_run=true`
- `order_submission_enabled=false`
- `real_order_submission_enabled=false`
- `exchange_private_access=false`

Se algum flag perigoso aparecer como verdadeiro, o painel mostra alerta de safety. Isso e visualizacao e auditoria, nao mecanismo de execucao.

## Por que e read-only

O dashboard nao pode executar trade diretamente. Esta tela nao tem botao de ordem, nao altera `.env`, nao altera Docker, nao altera `START_PAPER_24H` e nao chama API privada. Ela apenas le relatorios locais para acompanhamento e futura avaliacao.

## Nao libera live trading

Mesmo que o painel mostre `OK`, isso nao libera live trading. O resultado apenas apoia pesquisa/shadow e precisa continuar passando por governanca, anti-leakage, avaliacao contra baseline, RiskManager, ledger, preflight, kill switch e FinancialEventLog antes de qualquer mudanca operacional paper.
