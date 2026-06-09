# Paper/Shadow Soak Continuity and Gap Accounting

## Objetivo

Esta frente adiciona uma auditoria institucional de continuidade do soak paper/shadow. O relatório mede se há evidência temporal suficiente, identifica lacunas operacionais e preserva o contrato de segurança do projeto FUTUROS: nenhuma liberação live pode ser derivada automaticamente de métricas de soak.

## Contrato canônico

- 7 dias: janela diagnóstica operacional.
- 30 dias: requisito mínimo de readiness.
- 30 dias não liberam live automaticamente.
- Live/canário continua dependente de gates adicionais, governança manual e ausência de bloqueios P0/P1.

## Relatório gerado

A CLI `scripts/audit_paper_shadow_soak_continuity.py` gera, por padrão:

```text
data/reports/paper_shadow_soak_continuity_audit.json
```

O schema é:

```text
paper_shadow_soak_continuity_v1
```

Campos centrais:

- `status`: `ok`, `degraded`, `blocked` ou `evidence_missing`.
- `observed_calendar_days`: janela calendário observada.
- `observed_active_days`: soma de intervalos ativos quando disponível.
- `diagnostic_soak_reached`: verdadeiro quando atinge 7 dias.
- `readiness_soak_reached`: verdadeiro quando atinge 30 dias.
- `continuity_approved`: verdadeiro apenas quando a continuidade passa sem bloqueios.
- `live_release_allowed`: sempre `false`.
- `critical_gap_count`: número de gaps críticos.
- `warning_gap_count`: número de gaps de alerta.
- `missing_evidence`: evidências ausentes.
- `invalid_evidence`: evidências JSON inválidas.
- `safety_flags`: contrato paper/shadow only.

## Classificação dos gaps

A auditoria coleta timestamps e intervalos presentes nos relatórios existentes. Quando existem eventos temporais suficientes:

- Gap de alerta: intervalo maior que `--max-warning-gap-minutes`.
- Gap crítico: intervalo maior que `--max-critical-gap-minutes`.

Defaults:

```text
--max-warning-gap-minutes 60
--max-critical-gap-minutes 360
```

Um gap crítico bloqueia readiness mesmo que a janela de 30 dias tenha sido atingida.

## Fontes de evidência

A leitura é defensiva e tolerante a ausência de arquivos. Quando existirem, são consumidos relatórios como:

- `data/reports/paper_shadow_soak_report.json`
- `data/reports/paper_soak_report.json`
- `data/reports/runtime_evidence_pack_v2.json`
- `data/reports/readiness_snapshot_v2.json`
- `data/reports/freqtrade_paper_db_authority_report.json`
- `data/reports/phase14_feedback_sync_summary.json`
- `data/reports/ai_shadow_filter_incremental_daily_summary.json`
- `data/reports/ai_shadow_filter_decision_db_audit_summary.json`

Arquivo ausente entra em `missing_evidence`. JSON inválido entra em `invalid_evidence`. A auditoria não deve quebrar com stacktrace por evidência parcial.

## Status

### evidence_missing

Sem evidência mínima para estimar continuidade. Exemplos:

- falta `paper_shadow_soak_report`, `paper_soak_report`, `runtime_evidence_pack_v2` e `readiness_snapshot_v2`;
- evidências mínimas existem, mas estão inválidas.

### blocked

Há evidência mínima, mas existe bloqueio. Exemplos:

- menos de 30 dias observados;
- gap crítico;
- `sends_orders=true`;
- `changes_risk=true`;
- `exchange_private_access=true`;
- snapshot de readiness bloqueado;
- qualquer evidência sugerindo `live_release_allowed=true`.

### degraded

Há evidência suficiente, sem bloqueio crítico, mas com warning. Exemplos:

- gaps de alerta;
- evidências opcionais ausentes;
- JSON opcional inválido.

### ok

A janela mínima foi atingida, não há gaps críticos, não há violações de segurança e não há warnings. Mesmo assim, `live_release_allowed=false` permanece obrigatório.

## Uso

Auditoria sem escrita:

```powershell
python .\scripts\audit_paper_shadow_soak_continuity.py --no-write --json
```

Auditoria com escrita padrão:

```powershell
python .\scripts\audit_paper_shadow_soak_continuity.py
```

Parâmetros principais:

```powershell
python .\scripts\audit_paper_shadow_soak_continuity.py `
  --project-root . `
  --output data/reports/paper_shadow_soak_continuity_audit.json `
  --required-soak-days 30 `
  --diagnostic-soak-days 7 `
  --max-warning-gap-minutes 60 `
  --max-critical-gap-minutes 360
```

## Integração com Runtime Evidence Pack v2

O relatório é desenhado para ser consumido pelo Runtime Evidence Pack v2 e pelo Readiness Snapshot v2 como evidência read-only adicional. Ele não altera risco, não altera dataset, não escreve no `trades_master` e não envia ordens.

## Safety flags

O relatório sempre declara:

```json
{
  "paper_only": true,
  "shadow_only": true,
  "live_trading_enabled": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "exchange_private_access": false,
  "sends_orders": false,
  "changes_risk": false,
  "changes_training_dataset": false,
  "writes_trades_master": false,
  "live_release_allowed": false
}
```

Qualquer evidência de runtime que contradiga esse contrato bloqueia a auditoria.

## Fora de escopo

Esta branch não executa trading, não importa OCR, não reconstrói datasets, não altera IA Shadow e não habilita canário/live.
