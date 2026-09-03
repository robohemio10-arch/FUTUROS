# AIBOT Parity Paper A/B + Soak V1

## Objetivo

Esta fase inicia a coleta **prospectiva** de evidência Control × Treatment após o fechamento do Software DoD do AIBOT Parity V2.

Ela **não ativa o Treatment no Freqtrade**. O braço A/B existe somente na camada analítica. O Paper continua executando seu baseline atual sem alteração de stake, leverage, ROI, stoploss, universo, RiskManager, sinais ativos ou modelos.

## Âncora prospectiva

O pré-registro versionado está em:

`config/research/aibot_parity_paper_ab_soak_v1.json`

A janela começa em `2026-09-03T19:55:50Z`, timestamp do merge W14 / PR #373:

`2daa54c47b033b5951ebf6f2ff7fa615beab3ee8`

Qualquer linha com `observed_at_utc` anterior é excluída do sample prospectivo.

## Desenho A/B

O contrato de assignment reutiliza o material canônico existente:

`SHA256(experiment_id + "|" + candidate_id)`

- primeiro byte `< 128` → `CONTROL`;
- caso contrário → `TREATMENT`.

A alocação é **analítica**, não roteamento de tráfego.

### Control

`FREQTRADE_PAPER_BASELINE_OBSERVED_ONLY`

Usa o outcome Paper autoritativo observado, sem alterar a execução.

### Treatment

`AIBOT_PARITY_SHADOW_COUNTERFACTUAL_ONLY`

A decisão AIBOT já produzida em shadow é aplicada somente no cálculo:

- `ACCEPT` → outcome realizado entra no PnL contrafactual;
- `REJECT` ou `ABSTAIN` → PnL contrafactual = `0`;
- `ACCEPT` exige `riskmanager_shadow_decision=ALLOW`.

Nenhum desses estados é publicado no signal producer.

## Gates pré-registrados

Os thresholds reutilizam `paper_ab_edge_selector_v1`, sem criar uma segunda policy:

- mínimo de 200 outcomes por braço;
- mínimo de 45 dias observados;
- Profit Factor do Treatment >= 1.10;
- bootstrap determinístico com 5.000 iterações;
- seed `20260820`;
- confiança de 95%;
- evidência incremental exige limite inferior do CI da diferença de expectancy estritamente positivo.

Mesmo quando todos os gates passarem, a saída máxima é:

`INCREMENTAL_EDGE_RESEARCH_ONLY`

Isso apenas habilita uma **revisão futura separada**. Não autoriza Paper Treatment.

## Contrato das linhas prospectivas

Cada candidate row deve provar explicitamente, antes de entrar no sample:

- `candidate_id` e `cycle_id` estáveis;
- `observed_at_utc >= preregistered_start_utc`;
- `point_in_time_valid=true`;
- `financial_config_unchanged=true`;
- `paper_only=true` e `shadow_only=true`;
- `operational_authority=false`;
- `signal_published=false`;
- `writes_active_signals=false`;
- `sends_orders=false`;
- `changes_risk=false`;
- `changes_model=false`;
- `treatment_action` em `ACCEPT`, `REJECT`, `ABSTAIN`;
- outcome, quando disponível, precisa ter `outcome_available_at_utc > observed_at_utc`.

Ausência de prova de safety é fail-closed.

## Estados de evidência

- `EVIDENCE_BLOCKED`: integridade/lineage falhou ou ainda não há ambos os braços com outcomes;
- `INSUFFICIENT_SAMPLE`: evidência válida, mas 200/arm e/ou 45 dias ainda não foram atingidos;
- `PROMISING_NOT_PROVEN`: ponto estimado positivo sem prova estatística suficiente;
- `NO_INCREMENTAL_EDGE`: gates de edge não foram atendidos;
- `INCREMENTAL_EDGE_RESEARCH_ONLY`: evidência prospectiva apta somente para revisão de release futura.

## Soak health

O relatório contabiliza:

- linhas recebidas;
- assignments elegíveis;
- outcomes concluídos e pendentes;
- linhas pré-registro excluídas;
- duplicatas idênticas ignoradas idempotentemente;
- ciclos únicos;
- dias observados;
- contagem por braço;
- `ACCEPT/REJECT/ABSTAIN`;
- ocorrências `QLIB BLOCKED_EXTERNAL`;
- blockers de integridade.

Qlib bloqueado externamente é apenas contabilizado. Não há bypass nem update do runtime Qlib.

## Runner

Default, sem dados e sem escrita:

```powershell
python .\scripts\run_aibot_parity_paper_ab_soak_v1.py --project-root . --json
```

Avaliação de um arquivo normalizado explicitamente fornecido:

```powershell
python .\scripts\run_aibot_parity_paper_ab_soak_v1.py --project-root . --input-json .\data\reports\aibot_parity\prospective_candidate_rows.json --json
```

Persistência opcional de evidência research-only:

```powershell
python .\scripts\run_aibot_parity_paper_ab_soak_v1.py --project-root . --input-json .\data\reports\aibot_parity\prospective_candidate_rows.json --write-assignments --write-report --json
```

As escritas são restringidas a `data/reports/` pela persistência atômica já existente do Paper A/B Edge Selector.

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
changes_risk=false
changes_model=false
exchange_private_access=false
paper_treatment_release_allowed=false
paper_activation_performed=false
qlib_security_gate_bypassed=false
```

## Próximas fases

Esta V1 apenas inicia e governa a coleta prospectiva. Após amostra suficiente, uma branch separada deverá validar robustez adicional (walk-forward purged/embargo, Monte Carlo/stress, múltiplos testes/DSR ou equivalente já institucionalizado, sensibilidade e slices por regime/símbolo/lado/período) antes de qualquer proposta de ativação Paper.
