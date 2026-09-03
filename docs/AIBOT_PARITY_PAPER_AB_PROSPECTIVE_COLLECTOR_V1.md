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

Não existe matching heurístico por símbolo, timestamp, preço, ordem aproximada ou proximidade temporal. Sem `trade_link`, o outcome permanece pendente.

## Captura point-in-time

Uma nova observação somente é elegível quando:

- o snapshot AIBOT valida no contrato estrito;
- todos os required sources estão presentes e `point_in_time_status=VALID`;
- o snapshot não está `BLOCKED`;
- o `candidate_id` selecionado existe como Decision Record 4.2 selado;
- `decision_timestamp <= decision_time_utc` do snapshot;
- `ensemble_action` já é canonicamente `ACCEPT`, `REJECT` ou `ABSTAIN`;
- `ACCEPT` exige `riskmanager_shadow_decision=ALLOW`;
- as safety flags do snapshot provam Paper/Shadow/Research sem autoridade operacional;
- o operador/host fornece explicitamente `--assert-financial-config-unchanged`.

A última exigência é deliberada. A preregistração A/B atual não contém fingerprint financeiro capaz de provar automaticamente que stake, leverage, ROI, stoploss, universo e demais parâmetros financeiros permaneceram inalterados. O collector, portanto, não inventa essa prova.

## Imutabilidade e idempotência

As observações são persistidas em JSONL com:

- `observation_id = SHA256(cycle_id|candidate_id)` com prefixo `obs-`;
- hash do snapshot AIBOT;
- hash do Decision Record 4.2;
- `observation_sha256` sobre o payload da observação;
- lock interprocesso;
- escrita atômica restrita a `data/reports/aibot_parity/`;
- duplicata idêntica ignorada;
- conflito do mesmo `observation_id` bloqueado;
- reutilização do mesmo `candidate_id` em ciclos diferentes bloqueada.

Outcome posterior não reescreve a observação. O evaluator recebe uma projeção em memória da observação original acrescida do PnL e `close_time` somente depois que o `trade_link` explícito resolver o closed trade correspondente.

## Leitura do runtime

A leitura de Decision Ledger e closed trades exige simultaneamente:

- path explícito fornecido na CLI;
- `--allow-paper-runtime-read`.

Não existe acesso a exchange privada, API autenticada ou SQLite mutável. O source contract de closed trades existente continua sendo a fronteira read-only.

## Runner

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
  --assert-financial-config-unchanged `
  --write-observations `
  --write-assignments `
  --write-report `
  --json
```

Os placeholders acima são apenas documentação de argumentos de execução; o código não possui path runtime default nem placeholder funcional.

## Relação com o A/B + soak

O collector chama diretamente o evaluator já mergeado e preserva a preregistração existente:

- `experiment_id=aibot-parity-paper-ab-soak-v1`;
- início prospectivo `2026-09-03T19:55:50Z`;
- assignment analítico `SHA256(experiment_id|candidate_id)`;
- 200 outcomes por braço;
- 45 dias;
- Profit Factor Treatment >= 1.10;
- bootstrap determinístico 5.000 iterações, seed `20260820`, 95%.

Nenhuma segunda policy estatística é criada.

## Relógio da coleta

A execução de software e o CI **não provam** que a coleta recorrente esteja ativa no host Paper. Por isso todo relatório V1 mantém estruturalmente:

```text
collection_clock_started=false
prospective_collection_running_proven=false
collection_clock_reason=software_execution_alone_does_not_prove_paper_host_recurring_collection
```

Uma etapa posterior de deployment/host evidence deve provar execução recorrente real e continuidade das fontes antes que o início do relógio dos 200 outcomes por braço seja declarado.

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
```

Qlib continua `BLOCKED_EXTERNAL` quando aplicável. Este collector apenas carrega essa informação na evidência e nunca faz bypass, install, update de runtime, treino ou promoção.
