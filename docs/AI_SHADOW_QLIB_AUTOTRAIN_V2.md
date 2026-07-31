# B05 — IA Shadow, Qlib e Autotreinamento Governado V2

## Objetivo

Consolidar as fundações existentes de Qlib, IA Shadow, feedback paper, watermark,
quarentena e drift em um protocolo único de avaliação research-only. Esta B05
não cria uma segunda esteira de treino e não altera nenhum caminho operacional.

## Entregas

- harness multi-policy com decisões contrafactuais no mesmo candle;
- campos obrigatórios `would_enter`, `would_reject`, `expected_entry`,
  `expected_exit` e `counterfactual_pnl`;
- Brier score, reliability curve, ECE, precisão e expected value por bucket;
- Qlib rank score convertido apenas em percentil de rank para uso como proxy
  probabilístico research-only;
- gate de treino condicionado a dados novos, watermark avançado, hash novo,
  ausência de microbatch duplicado e amostra mínima;
- challengers restritos a quarantine/research;
- cadências separadas para operação, feedback, drift, smoke, treino completo e
  governança, sem registrar scheduler;
- overlay de calibration drift e expected-value drift sobre o monitor já
  existente de feature, label e regime drift.

## Fonte e autoridade

O modo padrão usa uma fixture sanitizada e retorna
`authoritative_result=false`. Um input externo explícito pode produzir evidência
research-only autoritativa quanto ao arquivo fornecido, mas nunca autoridade
operacional.

## Gate de autotreino

O treino research-only só é elegível quando todas as condições são verdadeiras:

1. `new_unique_trade_count > 0`;
2. `current_watermark > previous_watermark`;
3. `current_dataset_hash != previous_dataset_hash`;
4. o hash atual não existe em `prior_microbatch_hashes`;
5. `total_unique_sample_count >= min_training_sample_rows`.

A B05 não executa treino automaticamente, não promove candidatos e não escreve
registry ativo.

## Segurança

Permanecem fixos:

- `paper_only=true`;
- `shadow_only=true`;
- `research_only=true`;
- `live_trading_enabled=false`;
- `live_release_allowed=false`;
- `canary_release_allowed=false`;
- `order_submission_enabled=false`;
- `real_order_submission_enabled=false`;
- `exchange_private_access=false`;
- `sends_orders=false`;
- `changes_risk=false`;
- `changes_model=false`;
- `operational_authority=false`;
- `automatic_promotion=false`;
- `runtime_activation=false`.

## CLI

```powershell
python scripts/build_ai_shadow_qlib_autotrain_v2.py `
  --project-root . `
  --config config/ai_shadow_qlib_autotrain_v2.json `
  --no-write `
  --json
```

A escrita de relatório exige `--write-report` e usa o writer atômico B01 para
`data/reports`. Nenhum outro diretório é escrito.
