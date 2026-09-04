# AIBOT Parity Paper A/B Prospective Collector V1

## Objetivo

Este componente fecha o gap de **materialização prospectiva** entre o snapshot AIBOT Parity, o Decision Ledger 4.2 e os closed outcomes Paper já exportados em modo read-only.

Ele não ativa Treatment, não altera o Paper e não inicia, por si só, o relógio operacional do experimento. A finalidade é produzir evidência reexecutável para o evaluator `aibot_parity_paper_ab_soak_v1` já pré-registrado.

## Fontes autoritativas

O collector aceita somente fontes explícitas. Não existe default implícito para runtime Paper:

1. snapshot AIBOT Parity `aibot_parity_e2e_snapshot_v1`;
2. Decision Ledger 4.2 JSONL validado por schema e `payload_sha256`;
3. export read-only de closed trades Paper normalizado pelo contrato `paper_closed_trades_readonly_source_contract_v1`;
4. ledger próprio de observações prospectivas em `data/reports/aibot_parity/`.

O join de outcome é exclusivamente:

```text
candidate_id -> Decision Ledger trade_link -> trade_id -> closed trade Paper
```

Não existe matching heurístico por símbolo, timestamp, preço, ordem aproximada ou proximidade temporal. Sem `trade_link`, o outcome permanece pendente e isso não é, por si só, erro de integridade.

## Captura point-in-time fail-closed

Uma nova observação somente é elegível quando:

- o snapshot AIBOT valida no contrato estrito;
- todos os required sources estão presentes e `point_in_time_status=VALID`;
- o snapshot não está `BLOCKED`;
- o `candidate_id` selecionado existe como Decision Record 4.2 selado;
- `decision_timestamp <= decision_time_utc` do snapshot;
- `ensemble_action` é canonicamente `ACCEPT`, `REJECT` ou `ABSTAIN`;
- `ACCEPT` exige `riskmanager_shadow_decision=ALLOW`;
- as safety flags do snapshot provam Paper/Shadow/Research sem autoridade operacional;
- a configuração financeira Paper atual produz exatamente o fingerprint pré-registrado pela runtime foundation.

Snapshot `BLOCKED`, required source ausente/PIT inválido ou fingerprint divergente bloqueiam a captura antes da criação da observação.

## Fingerprint financeiro automático

A runtime foundation materializa `paper_financial_config_sha256` a partir de um payload canônico que cobre somente parâmetros econômicos relevantes do cohort Paper e sem projetar secrets. A configuração de referência está em:

```text
config/research/aibot_parity_prospective_runtime_foundation_v1.json
```

O fingerprint cobre, entre outros, stake, ROI, stoploss, max open trades, universo/pairlist, trading/margin mode, parâmetros de exit/pricing relevantes, semântica de leverage e hash canônico LF do arquivo completo da estratégia configurada. A semântica de leverage é selada pelo hash do trecho-fonte normalizado, e não por serialização interna de AST dependente da versão do interpretador. Assim, mudança de lógica da estratégia também invalida o baseline, enquanto simples diferença CRLF/LF ou execução em Python 3.12/3.13 não altera o fingerprint.

`exchange.key`, `exchange.secret`, tokens, senhas e demais credenciais não entram no payload econômico. Os hashes brutos dos arquivos fonte são mantidos como metadata de auditoria; adicionalmente, o hash canônico LF da estratégia faz parte do fingerprint econômico para bloquear qualquer mudança de comportamento da estratégia durante o cohort.

A flag histórica `--assert-financial-config-unchanged` permanece somente como metadata backward-compatible; ela não é mais aceita como prova suficiente.

## Imutabilidade e idempotência

As novas observações usam `aibot_parity_paper_ab_prospective_observation_v2` e são persistidas em JSONL com:

- `observation_id = SHA256(cycle_id|candidate_id)` com prefixo `obs-`;
- `observed_at_utc` igual ao tempo de decisão;
- `captured_at_utc` igual ao instante real de materialização;
- `collector_run_id` único por execução;
- `paper_financial_config_sha256`;
- hash do snapshot AIBOT;
- hash do Decision Record 4.2;
- `observation_sha256` sobre todo o payload da observação;
- lock interprocesso;
- escrita atômica restrita a `data/reports/aibot_parity/`;
- duplicata idêntica ignorada;
- conflito do mesmo `observation_id` bloqueado;
- reutilização do mesmo `candidate_id` em ciclos diferentes bloqueada também no writer direto.

O leitor continua aceitando observações V1 já existentes para compatibilidade e auditoria, mas elas são excluídas da avaliação prospectiva da runtime foundation porque não possuem prova de `captured_at_utc`/fingerprint. Replay de uma observação V2 existente preserva o `captured_at_utc` original; o outcome posterior não reescreve a observação.

O evaluator recebe uma projeção em memória da observação original acrescida do PnL e `outcome_available_at_utc` somente depois que o `trade_link` explícito resolver o closed trade correspondente.

## Leitura do runtime

A leitura de Decision Ledger e closed trades exige simultaneamente:

- path explícito fornecido na CLI;
- `--allow-paper-runtime-read`.

Não existe acesso a exchange privada, API autenticada ou SQLite mutável. O source contract de closed trades existente continua sendo a fronteira read-only.

## Runner do collector

Inspeção sem runtime Paper e sem escrita:

```powershell
python .\scripts\run_aibot_parity_paper_ab_prospective_collector_v1.py --project-root . --json
```

Captura de uma nova observação com fontes explicitamente autorizadas:

```powershell
python .\scripts\run_aibot_parity_paper_ab_prospective_collector_v1.py `
  --project-root . `
  --aibot-snapshot-json .\data\reports\aibot_parity\aibot_parity_e2e_snapshot_v1.json `
  --decision-ledger-jsonl <CAMINHO_EXPLICITO_DO_LEDGER_PAPER> `
  --closed-trades-path <CAMINHO_EXPLICITO_DO_EXPORT_CLOSED_TRADES> `
  --allow-paper-runtime-read `
  --write-observations `
  --write-assignments `
  --write-report `
  --json
```

O runner retorna exit code `0` somente para `status=ok`; qualquer `blocked`/erro retorna não-zero.

Os placeholders acima são apenas documentação de argumentos de execução; o código não possui path runtime default nem placeholder funcional.

## Runtime activation foundation

A foundation adiciona um **runner de ciclo único**, adequado para agendamento externo no host Windows, sem registrar scheduler automaticamente:

```powershell
python .\scripts\run_aibot_parity_prospective_runtime_cycle_v1.py `
  --project-root . `
  --aibot-snapshot-json <SNAPSHOT_AIBOT_EXPLICITO> `
  --decision-ledger-jsonl <LEDGER_PAPER_EXPLICITO> `
  --closed-trades-path <CLOSED_TRADES_EXPLICITO> `
  --allow-paper-runtime-read `
  --write-evidence `
  --write-heartbeat `
  --json
```

O ciclo é lock-serialized, restart-safe e idempotente. Ele valida fingerprint financeiro, freshness, snapshot PIT, Decision Ledger, outcomes e depois materializa heartbeat/health. Exceções dentro do ciclo produzem heartbeat/health `blocked` quando `--write-heartbeat` foi solicitado.

`last_valid_observation_utc` é derivado exclusivamente de observações V2 já persistidas no ledger e do respectivo `captured_at_utc`; observações V1 legadas nunca avançam esse marcador. Ciclos `blocked` preservam o valor anterior e não promovem observações existentes apenas em memória para evidência de heartbeat.

Healthcheck read-only:

```powershell
python .\scripts\check_aibot_parity_prospective_runtime_health_v1.py `
  --project-root . `
  --json
```

O healthcheck bloqueia heartbeat ausente/stale/unsafe e também reporta contenção do lock do ciclo quando detectável.

A branch apenas declara:

```text
recurring_runner_available=true
scheduler_registration_performed=false
recurring_collection_proven=false
```

Nenhum serviço Docker novo ou tarefa Windows é registrado automaticamente nesta foundation.

## Relação com o A/B + soak

O collector chama diretamente o evaluator já mergeado e preserva a preregistração existente:

- `experiment_id=aibot-parity-paper-ab-soak-v1`;
- início prospectivo `2026-09-03T19:55:50Z`;
- assignment analítico `SHA256(experiment_id|candidate_id)`;
- 200 outcomes por braço;
- 45 dias;
- Profit Factor Treatment >= 1.10;
- bootstrap determinístico 5.000 iterações, seed `20260820`, 95%.

Nenhuma segunda policy estatística é criada. Insuficiência normal de amostra continua sendo estado de coleta; apenas blockers de integridade do evaluator são propagados ao status superior do collector.

## Relógio da coleta

A execução de software, CI, heartbeat ou healthcheck **não provam** que a coleta recorrente esteja ativa no host Paper. Por isso todo relatório desta foundation mantém estruturalmente:

```text
collection_clock_started=false
prospective_collection_running_proven=false
recurring_collection_proven=false
```

Uma etapa posterior de deployment/host evidence deve provar execução recorrente real, continuidade das fontes e primeira observação prospectiva válida antes que o relógio dos 200 outcomes por braço / mínimo 45 dias seja declarado iniciado.

## Invariantes absolutos

```text
paper_only=true
shadow_only=true
research_only=true
operational_authority=false
traffic_split_performed=false
paper_behavior_changed=false
treatment_runtime_assignment_performed=false
writes_active_signals=false
signal_published=false
sends_orders=false
exchange_private_access=false
changes_strategy=false
changes_risk=false
changes_stake=false
changes_leverage=false
changes_roi=false
changes_stoploss=false
changes_universe=false
changes_model=false
paper_treatment_release_allowed=false
paper_activation_performed=false
qlib_security_gate_bypassed=false
collection_clock_started=false
prospective_collection_running_proven=false
live=false
canary=false
real_order_submission=false
```

Qlib continua `BLOCKED_EXTERNAL` quando aplicável. Este collector apenas carrega essa informação na evidência e nunca faz bypass, install, update de runtime, treino ou promoção.
