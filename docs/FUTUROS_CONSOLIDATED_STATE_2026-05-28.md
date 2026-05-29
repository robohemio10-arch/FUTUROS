# FUTUROS Consolidated State - 2026-05-28

Este documento consolida os dois handovers paralelos do projeto FUTUROS/SmartCrypto e fixa o estado operacional autorizado em 2026-05-28.

## ProjectRoot Oficial

O ProjectRoot oficial é:

```text
E:\FUTUROS
```

Qualquer comando operacional, validação ou inspeção deve partir desse diretório, salvo diagnóstico explicitamente isolado.

## Estado Git Validado

A frente OCR/IA Shadow foi validada após merge da PR `codex/vital-ocr-shadow-contracts` na `dev`.

Estado validado informado:

- `python -m compileall smartcrypto scripts tests`: OK.
- `python -m pytest`: 330 passed.
- working tree clean.
- Projeto em modo paper/shadow.
- live trading bloqueado.
- Sem ordem real.
- Sem leitura privada de exchange.

## Estado Atual Dos Datasets

Os números atuais oficiais são:

- `trades_master.xlsx`: 2864 linhas.
- `trades_master.parquet`: 2864 linhas.
- `trade_enriched.parquet`: 2864 linhas.
- `training_dataset.parquet`: 2864 linhas.
- `training_dataset_quality_gated_binance_1m.parquet`: 2631 linhas.

Esses dados substituem qualquer número histórico menor usado em handovers antigos.

## Estado IA Shadow

A IA Shadow é observador/filtro em paper/shadow. Ela não executa ordem e não escreve sinal operacional.

Estado atual informado:

- `ai_shadow_filter_decisions.sqlite`: 2631 linhas.
- `AI_ACCEPT`: 1600.
- `AI_REJECT`: 1031.
- `missing`: 0.
- `extra`: 0.
- órfãs removidas: 220.

A IA Shadow não é autoridade de execução. Ela não pode liberar live trading, aumentar risco, escrever `active_freqtrade_signals.json` ou substituir Freqtrade.

## Estado OCR Bitradex

OCR Bitradex foi importado como etapa controlada e revisável:

- OCR Bitradex importado: 76 trades.

OCR não altera dataset oficial automaticamente fora do fluxo permitido. A importação e rebuild de trades/datasets continuam sob autoridade da Fase 5.

## Estado Qlib Pendente

Qlib continua sendo research/scoring. Qlib não executa ordem.

O ponto pendente é operacionalizar com segurança a sequência:

1. market features fresh;
2. Qlib prediction fresh;
3. Phase13 gera sinal operacional;
4. Freqtrade consome somente o pinned signal autorizado.

Enquanto a recência de features/predições não estiver garantida, sinais devem ser bloqueados por guardrails de freshness.

## Números Históricos Desatualizados

Os números abaixo são históricos/desatualizados e não devem ser usados como estado atual:

- `trades_master`: 228 linhas.
- `trade_enriched`: 8 linhas.
- `training_dataset`: 8 linhas.

Eles podem aparecer em handovers antigos como contexto histórico, mas não têm autoridade operacional em 2026-05-28.

## Regra Operacional Absoluta

O projeto permanece paper/shadow only:

- live trading bloqueado.
- `ORDER_SUBMISSION_ENABLED` bloqueado.
- `REAL_ORDER_SUBMISSION_ENABLED` bloqueado.
- Não usar chaves reais.
- Não ler conta privada real.
- Não enviar ordem real.
- Não versionar `data/`, logs, SQLite, Parquet, CSV, XLSX, JSON runtime ou zip evidence.
- Dashboard é observabilidade/read-only.
- Fase 5 é a única via de importação/rebuild de trades/datasets.
- Fase 14 é o coletor oficial de feedback paper.
- Freqtrade é o único executor paper/dry-run.
