# Paper Autotrain Feedback Gap Diagnostics V1

## Objetivo

Explicar, com evidencia (nao com hipotese), por que trades paper fechados
presentes no paper DB e no CSV de closed trades estao ausentes do JSONL de
feedback do autotrain (`missing_in_feedback`, conforme
`paper_autotrain_source_key_reconciliation`).

Este modulo e **100% read-only** em relacao ao pipeline auditado: nunca cria
microbatch, nunca treina, nunca promove, nunca registra scheduler, nunca
altera watermark, e nao tem autoridade sobre Freqtrade, RiskManager, Qlib
runtime, IA Shadow runtime ou active signals. O unico escrito opcional e o
par de relatorios (`.json`/`.md`) sob `data/reports`, atras da flag explicita
`--write-report`.

## Por que este modulo existe

`paper_autotrain_source_key_reconciliation` ja produz `reconciled_group_count`,
`missing_in_feedback_count` etc., mas:

1. amostra cada classificacao em no maximo 25 grupos
   (`classify_groups()`'s `if len(samples[classification]) < 25`), entao um
   gap de 41 registros nunca aparecia completo em um unico relatorio;
2. nao verifica se existe mais de um escritor dos dois artefatos do gap
   (`paper_closed_trades_incremental.parquet`,
   `paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`);
3. nao distingue "o registro sumiu porque o pipeline nao rodou" de "o
   registro seria rejeitado pela validacao mesmo se o pipeline rodasse".

Este modulo reusa a logica de carregamento/reconciliacao de
`paper_autotrain_source_key_reconciliation` (mesmas fontes, mesmas chaves de
reconciliacao) em vez de reimplementa-la, e adiciona exatamente essas tres
lacunas.

## Busca de writers (escopo 1/2)

`search_writers(root)` faz uma busca estatica, read-only, full-repo (nunca
importa nem executa codigo do repositorio) em duas camadas:

1. Busca exata por `def write_feedback_outputs(` e `def write_quarantine_outputs(`
   (zero falso-positivo).
2. Uma heuristica com escopo por funcao e ciente do argumento: para cada
   funcao do arquivo, resolve (i) constantes de modulo cujo literal contem o
   nome-alvo, (ii) parametros cujo default e uma dessas constantes, e so
   conta como candidato a escritor se uma chamada real de escrita
   (`.to_parquet(X)`, `.to_csv(X)`, `X.write_text(...)`, `X.write(...)`) usa
   exatamente esse nome como argumento/receiver. Isso evita marcar como
   "escritor" uma funcao que so le o caminho-alvo e escreve, na mesma funcao,
   uma saida completamente diferente.

**Limitacao explicita:** e uma heuristica de um unico salto, no mesmo arquivo
— nao segue imports entre arquivos alem da busca exata pelas duas funcoes
conhecidas, e nao e uma prova formal de exaustividade. Toda ocorrencia
individual fica em `writer_search_matches` para conferencia manual.

**Achado confirmado nesta rodada** (antes so respondido por leitura manual,
agora por busca real no repo clonado): existem **dois** escritores
independentes de `paper_closed_trades_incremental.parquet` —
`smartcrypto/learning/paper_autolearning/feedback_store.py::write_feedback_outputs()`
(o caminho conhecido, via `daily_foundation_runner.py`) e
`scripts/update_paper_feedback_incremental_store.py::update_incremental_store()`
(um script standalone, **nao referenciado em `docker-compose.paper.yml`, Makefile
ou qualquer scheduler** — so aparece em `docs/PAPER_FEEDBACK_INCREMENTAL_STORE.md`).
O segundo le um CSV de entrada diferente
(`data/trades/inbox/freqtrade_paper_closed_trades.csv`, schema
`moeda/fechar_side/horario_abertura/...`) e faz merge incremental proprio
(dedup por `order_id_first_then_fingerprint`), nao um overwrite cego. Ha
exatamente **um** escritor do JSONL de feedback (`activation.py`), como ja
confirmado em rodadas anteriores.

## Listagem completa de ausentes (escopo 3/4/5)

`build_missing_record_rows()` itera o dicionario de grupos inteiro (nao a
amostra truncada) e devolve uma linha por grupo `missing_in_feedback`, com
todos os campos: `classification`, `dedup_key` (a `reconciliation_key`),
`native_key`, `source_keys`, `paper_db_trade_id`, `closed_trades_csv_order_id`,
`symbol`, `side`, `open_time_utc`, `close_time_utc`, `net_pnl`, `profit_ratio`,
`source_presence`, `missing_sources`, `db_csv_match_status`,
`normalization_status`, `validation_status`, `causal_bucket`.

`validation_status` roda de verdade (nao especula) a normalizacao/validacao
real da Etapa 1 (`normalize_closed_trade_row` + `validate_event_inputs` de
`feedback_store.py`) e da Etapa 2 (`normalize_closed_trades` de
`activation.py`) contra o payload bruto de cada registro ausente.

## Separacao cadencia vs validacao (escopo 6)

- `cadence_gap_mechanism_status`: compara os `close_time_utc` dos ausentes
  contra o mtime real do output da Etapa 1
  (`data/feedback/paper_closed_trades_incremental.parquet`). `confirmed`
  quando todos os ausentes sao posteriores a essa escrita; `indeterminate_missing_evidence`
  quando o arquivo nao existe neste checkout (por exemplo, este repositorio
  clonado via `git bundle` nao tem `data/`, que e gitignored).
- `validation_rejection_status`: conta quantos ausentes tem
  `would_pass_both_stages=False`, com a lista de `dedup_key` rejeitados e um
  `causal_bucket_counts` agregado.

As duas nunca sao colapsadas em um unico campo: um registro pode estar no
gap por cadencia pura (`cadence_gap_unexplained_by_validation`) mesmo que
outro, no mesmo lote, esteja la por falha de validacao
(`validation_rejection:<motivo>`).

## Uso

```bash
python scripts/build_paper_autotrain_feedback_gap_diagnostics_v1.py --project-root . --json
python scripts/build_paper_autotrain_feedback_gap_diagnostics_v1.py --project-root . --allow-paper-db-read --write-report --json
```

`--allow-paper-db-read` e opcional e so autoriza leitura read-only do SQLite
paper (runtime ou snapshot, via a mesma logica de autoridade de
`paper_autotrain_paper_runtime_source_diagnostics.resolve_paper_db`); sem
essa flag, a reconciliacao roda so com CSV + feedback JSONL.

## Safety flags

`paper_only`, `shadow_only`, `research_only`, `read_only` sao sempre `true`.
`write_performed`/`write_report_performed` comecam `false` e so viram `true`
quando `--write-report` e passado e o caminho de saida valida sob
`data/reports`. Todas as demais flags de risco/promocao/scheduler/ordem
listadas em `safety_flags()` sao hard-coded `false` e nunca dependem de
input do usuario.

## Proibido nesta branch

Backfill automatico, criacao de microbatch, treino, promocao, alteracao de
watermark, criacao de scheduler, escrita em `data/feedback`/`data/runtime`,
e qualquer alteracao em Freqtrade, RiskManager, Qlib runtime, IA Shadow
runtime ou active signals.
