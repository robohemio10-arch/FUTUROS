# Paper Autotrain Evidence Accumulation Window V1

## Objetivo

Esta branch (65) implementa um acumulador **research-only** e **read-only por padrão** de microbatches
gerados pela ativação diária de autotreinamento paper em quarentena (Branch 63). Ela responde a uma
única pergunta: **já existe evidência mínima acumulada, ao longo do tempo, para justificar uma futura
reavaliação de candidatos de quarentena em uma branch separada?**

Ela não treina, não promove, não altera contrato ativo, não altera dataset manifest ativo, não escreve
registry ativo, não escreve modelo ativo, não altera a quarentena, não atualiza runtime do Qlib, da IA
Shadow, do Freqtrade ou do RiskManager, e não envia ordens.

## Por que a Branch 64 bloqueou os dois candidatos

A Branch 64 (`paper_autotrain_quarantine_candidate_evaluation`) avaliou os 2 candidatos de quarentena
(`qlib_...` e `ai_shadow_...`) gerados pela última ativação e bloqueou ambos com
`decision=MANTER_EM_QUARENTENA`, porque o microbatch mais recente tinha:

- `observed_microbatch_rows=26` (mínimo exigido: `100`);
- `observed_class_negative_count=19` (mínimo exigido: `20`);
- `observed_class_positive_count=7` (mínimo exigido: `20`);
- `eligible_candidate_count=0`.

Ou seja: os candidatos até tinham integridade de artefato (`artifact_integrity_status=ok`), mas a
evidência estatística por trás deles era insuficiente. Rodar `retrain` de novo no mesmo dia não resolve
isso — o problema é volume e balanceamento de classes ao longo do tempo, não o processo de treino em si.

## Diferença entre acumular evidência e treinar/promover

Esta branch **não é** uma nova tentativa de treino. Ela não chama nenhum backend de treino (Qlib, IA
Shadow), não gera nenhum artefato de modelo, e não decide sobre promoção. O que ela faz é puramente
estatístico e read-only:

1. Descobre todos os microbatches históricos já gerados pela Branch 63.
2. Acumula (concatena) essas linhas.
3. Deduplica de forma determinística (a mesma execução, com os mesmos arquivos de entrada, sempre produz
   o mesmo resultado).
4. Calcula se o volume e o balanceamento de classes acumulados, **depois** da deduplicação, atingem os
   mínimos estatísticos.
5. Retorna `candidate_recheck_allowed=true` apenas quando isso acontece — e mesmo assim, isso só libera
   uma **futura branch separada** para reavaliar candidatos; não treina nem promove nada aqui.

Mesmo quando `status=ok` e `candidate_recheck_allowed=true`, os campos `training_allowed`,
`promotion_allowed` e `runtime_allowed` permanecem `false` sempre. Essa branch nunca os libera.

## Por que deduplicar importa

Os microbatches diários da quarentena são gerados a cada execução da ativação (Branch 63), mas nem
sempre carregam trades novos — se o feedback paper subjacente não mudou, o mesmo conjunto de linhas pode
aparecer em múltiplos snapshots com o mesmo `record_hash`. Sem deduplicação, `source_row_count`
cresceria artificialmente a cada execução repetida, sem representar evidência nova de fato. Por isso o
acumulador calcula `duplicate_rate` e bloqueia quando ela ultrapassa `max_duplicate_rate=0.05` — um
`duplicate_rate` alto é, em si, um sinal de que ainda não há evidência genuinamente nova, mesmo que o
volume bruto pareça grande.

## Critérios mínimos

| Critério | Valor mínimo/máximo |
|---|---|
| `min_accumulated_rows` | `100` |
| `min_class_positive_count` | `20` |
| `min_class_negative_count` | `20` |
| `min_feature_count` | `5` |
| `min_distinct_run_ids` | `1` |
| `max_duplicate_rate` | `0.05` |

Todos os critérios precisam ser atendidos simultaneamente, **depois** da deduplicação, para
`status=ok`. Qualquer critério não atendido produz `status=blocked`,
`reason=insufficient_accumulated_evidence` — a lista completa de critérios não atendidos aparece em
`blockers` (mais de um pode aparecer ao mesmo tempo).

## Algoritmo de deduplicação

A primeira estratégia viável, nesta ordem de prioridade, é usada (viável = as colunas necessárias
existem no conjunto acumulado):

1. **`event_id`** — se essa coluna existir, é usada isoladamente como chave.
2. **`trade_id`/`order_id` + `close_time`** — usa `trade_id` (preferencial) ou `order_id`, combinado com
   `close_time_utc` ou `close_time`.
3. **`symbol` + `side` + `open_time` + `close_time` + `net_pnl`** — usa `net_pnl` ou, como alias, o
   campo real `pnl_fechado` presente nos microbatches atuais.
4. **Hash estável da linha normalizada** — usado apenas quando nenhuma das estratégias acima é viável;
   calcula um SHA-256 determinístico sobre todas as colunas de conteúdo da linha (ordenadas
   alfabeticamente, excluindo colunas internas de proveniência), garantindo que a mesma linha sempre
   produza a mesma chave.

Nos microbatches reais do repositório hoje não existe `event_id`, mas `order_id` e `close_time_utc`
existem — por isso a estratégia 2 (`trade_or_order_id_close_time`) é a selecionada na prática, e o
campo `dedup_key_strategy_used` no relatório confirma isso.

Linhas com valor nulo em qualquer coluna-chave nunca são deduplicadas entre si por engano — recebem uma
chave própria baseada na posição, para evitar colapsar dados incompletos de forma incorreta.

## Entradas permitidas

Fonte primária, obrigatória para haver qualquer evidência:

- `data/research/paper_autotrain_daily_quarantine/**/incremental_training_microbatch.parquet`

Caminhos adicionalmente permitidos (não são lidos pela lógica atual, mas fazem parte do escopo de leitura
autorizado desta branch caso uma extensão futura precise deles para contexto/validação cruzada):

- `data/feedback/paper_autotrain_daily_quarantine_feedback_events_v1.jsonl`
- `data/reports/paper_autotrain_daily_quarantine_activation_v1.json`
- `data/reports/paper_autotrain_quarantine_candidate_evaluation_v1.json`
- `data/registries/quarantine/paper_autotrain_candidate_registry_v1.json`

Nenhum desses arquivos é escrito ou alterado por esta branch.

## Saídas permitidas

Por padrão, nada é escrito. Cada categoria de escrita exige sua própria flag explícita:

Com `--write-report`, somente:

- `data/reports/paper_autotrain_evidence_accumulation_window_v1.json`
- `data/reports/paper_autotrain_evidence_accumulation_window_v1.md`

Com `--write-accumulated-dataset`, somente:

- `data/research/paper_autotrain_evidence_accumulation_window/accumulated_microbatch.parquet`
- `data/research/paper_autotrain_evidence_accumulation_window/accumulated_microbatch_manifest.json`

O dataset acumulado escrito já está deduplicado (nunca é o dataset bruto concatenado) e nunca contém as
colunas internas de proveniência usadas durante o processamento.

## Paths proibidos

Esta branch nunca escreve em:

- `data/runtime/`
- `data/registries/` fora do que já existir (nenhuma escrita, nem no registry de quarentena);
- `data/models/` (nenhum modelo ativo ou de quarentena é escrito por esta branch);
- qualquer `active_freqtrade_signals.json` ou outro arquivo de sinal operacional;
- SQLite;
- Parquet fora de `data/research/paper_autotrain_evidence_accumulation_window/`;
- qualquer scheduler (cron, systemd timer, Windows Task, serviço).

O domínio (`smartcrypto/learning/paper_autotrain_evidence_accumulation_window/`) nunca importa
`freqtrade`, `ccxt`, `docker` ou o `RiskManager`, e nunca chama `subprocess`.

## Comandos

Modo padrão, sem escrita:

```powershell
python .\scripts\build_paper_autotrain_evidence_accumulation_window_v1.py --project-root . --json
```

Gerar evidência JSON/Markdown em `data/reports`:

```powershell
python .\scripts\build_paper_autotrain_evidence_accumulation_window_v1.py --project-root . --write-report --json
```

Gerar o dataset acumulado e deduplicado em `data/research/paper_autotrain_evidence_accumulation_window`:

```powershell
python .\scripts\build_paper_autotrain_evidence_accumulation_window_v1.py --project-root . --write-accumulated-dataset --json
```

Ambos ao mesmo tempo:

```powershell
python .\scripts\build_paper_autotrain_evidence_accumulation_window_v1.py --project-root . --write-report --write-accumulated-dataset --json
```

Validar limites de escrita sem escrever nada (útil em CI):

```powershell
python .\scripts\build_paper_autotrain_evidence_accumulation_window_v1.py --project-root . --fail-on-operational-write --json
```

## Interpretação dos status

| Condição | `status` | `reason` | `decision` |
|---|---|---|---|
| Nenhum microbatch encontrado | `blocked` | `missing_quarantine_microbatch_sources` | `AGUARDAR_MAIS_EVIDENCIA` |
| Microbatches encontrados, mas abaixo de algum mínimo (linhas, classes, features, taxa de duplicidade) | `blocked` | `insufficient_accumulated_evidence` | `AGUARDAR_MAIS_EVIDENCIA` |
| Todos os mínimos atingidos após deduplicação | `ok` | `accumulated_evidence_ready_for_candidate_recheck` | `REAVALIACAO_DE_CANDIDATOS_PERMITIDA_EM_BRANCH_SEPARADA` |

`accumulation_ready_for_candidate_recheck` e `candidate_recheck_allowed` só são `true` na terceira
linha. `training_allowed`, `promotion_allowed` e `runtime_allowed` são **sempre** `false`,
independentemente do status.

## Resultado real esperado hoje

Com os 5 microbatches atualmente presentes em
`data/research/paper_autotrain_daily_quarantine/`, todos contendo exatamente as mesmas 26 linhas
(mesmo `record_hash`), o resultado esperado é:

- `status=blocked`;
- `reason=insufficient_accumulated_evidence`;
- `decision=AGUARDAR_MAIS_EVIDENCIA`;
- `source_file_count=5`, `source_row_count=130`;
- `dedup_key_strategy_used=trade_or_order_id_close_time`;
- `accumulated_row_count=26` (após deduplicação), `duplicate_rows_removed=104`,
  `duplicate_rate=0.8`;
- `accumulation_ready_for_candidate_recheck=false`, `candidate_recheck_allowed=false`.

## Próxima branch quando `candidate_recheck_allowed=true`

Quando este acumulador reportar `status=ok` e `candidate_recheck_allowed=true`, isso autoriza — mas não
executa — uma **branch futura separada** para reavaliar os candidatos de quarentena (repetindo o padrão
da Branch 64, `paper_autotrain_quarantine_candidate_evaluation`) usando o dataset acumulado e
deduplicado como evidência de entrada, em vez do microbatch mais recente isolado. Essa branch futura
continuará sujeita às mesmas regras: research-only, sem treino automático de produção, sem promoção
automática, e com `RiskManager`/Freqtrade/Qlib runtime fora de alcance.
