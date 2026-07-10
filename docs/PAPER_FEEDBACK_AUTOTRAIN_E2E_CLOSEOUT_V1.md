# Paper Feedback Autotrain E2E Closeout V1

## Objetivo

Este pacote consolida o diagnostico, o plano, o dry-run, um executor de backfill
controlado e a reavaliacao read-only dos gates de autotrain paper. A operacao
continua paper-only, shadow-only e research-only.

O comando default nao escreve e retorna
`NO_BACKFILL_WITHOUT_EXPLICIT_AUTHORIZATION`. A entrega nao executa treino,
promocao, registry ativo, microbatch, sinal, ordem ou alteracao de runtime.

## Writers Identificados

A busca estatica do repositorio identificou os writers canonicos anteriores:

- `smartcrypto.learning.paper_autolearning.feedback_store.write_feedback_outputs`:
  feedback store e outcome events em Parquet;
- `smartcrypto.learning.paper_autotrain_daily_quarantine_activation.write_quarantine_outputs`:
  artefatos de quarentena e feedback JSONL.

O novo writer autorizado e isolado e
`paper_feedback_autotrain_e2e_closeout.controlled_backfill.execute_controlled_backfill`.
Ele recebe somente o path canonico do JSONL e nao chama os writers anteriores.
Qualquer writer inesperado reportado pelo diagnostico bloqueia a operacao.

## Fases

### Discovery

Os paths sao resolvidos sob o project root. Symlinks, extensoes incorretas,
feedback ausente, output fora de `data/reports` e writer inesperado bloqueiam o
fluxo. O fingerprint combina SHA-256 e tamanho das fontes externas.

### Recomputation

O orquestrador reutiliza os contratos existentes para:

1. diagnostico do gap;
2. remediation plan em memoria;
3. dry-run e eventos candidatos em memoria;
4. hashes atuais de plano, dry-run e fontes.

As contagens e hashes historicos nunca sao autoridade.

### Authorization

Mutacao exige simultaneamente:

- `--execute-backfill`;
- `--expected-plan-hash` igual ao hash recomputado;
- `--expected-dryrun-hash` igual ao hash recomputado;
- `--authorization-reference` sanitizada;
- `--confirmation-text` exatamente
  `EXECUTAR BACKFILL CONTROLADO DE FEEDBACK PAPER`.

Essa validacao ocorre antes de lock, backup ou escrita.

### Lock

O lock e criado com `O_CREAT | O_EXCL` no diretorio do feedback store. Ele
registra apenas PID, horario UTC, operation ID e referencia sanitizada. Lock de
terceiro nunca e removido. Se um rollback falhar, o lock proprio permanece para
intervencao manual.

### Backup

O backup e byte-exact, exclusivo e identificado pelo operation ID. O arquivo e
sincronizado com `fsync` e seu SHA-256 precisa coincidir com o original antes da
continuacao.

### Atomic Replace

Append in-place e proibido. O JSONL completo e montado em memoria, escrito em
temporario no mesmo filesystem, validado, sincronizado e substituido via
`os.replace`. As fontes externas sao re-hashadas imediatamente antes e depois
da substituicao.

### Post-write Audit And Rollback

A auditoria confirma contagem, unicidade, presenca de cada evento autorizado,
schema JSONL e imutabilidade das fontes externas. Falha restaura o backup por
replace atomico. Falha no rollback retorna `MANUAL_INTERVENTION_REQUIRED`.

### Idempotency

Se todos os eventos do lote ja existirem exatamente uma vez, nenhuma escrita ou
backup ocorre e a decisao e `BACKFILL_ALREADY_APPLIED`.

### Autotrain Readiness

Somente apos backfill validado ou lote ja aplicado, o pacote consulta sem escrita:

- continuidade do feedback;
- watermark e freshness;
- microbatch sync planner;
- backend Qlib sem treino;
- walk-forward e leakage;
- execution-cost gate;
- drift/regime gate;
- candidate registry gate sem escrita.

`READY_FOR_PAPER_OBSERVATION` exige todos os gates aprovados. Qualquer amostra
insuficiente, backend indisponivel, leakage ou blocker institucional preserva
`MANTER_EM_RESEARCH`.

## Relatorios

`--write-report` pode criar somente:

- `data/reports/paper_feedback_autotrain_e2e_closeout_v1.json`;
- `data/reports/paper_feedback_autotrain_e2e_closeout_v1.md`.

O relatorio nao inclui eventos, payload JSONL, credenciais, `.env`, headers,
respostas HTTP ou material secreto.

## Comandos Seguros

```powershell
python scripts/run_paper_feedback_autotrain_e2e_closeout_v1.py --project-root . --json
python scripts/run_paper_feedback_autotrain_e2e_closeout_v1.py --project-root . --write-report --json
```

O comando com `--execute-backfill` deve ser usado somente em uma janela
operacional futura, depois de revisao humana dos hashes recomputados. Ele nao faz
parte da validacao desta branch.

## Fora De Escopo

Nao sao alterados Docker, `.env`, Freqtrade, RiskManager, Qlib runtime, IA
Shadow runtime, modelos, registry ativo, sinais, scheduler, containers, exchange
privada ou ordens.
