# Runtime Evidence Sidecar Bundle

## Objetivo

O sidecar separa corretamente duas camadas:

- código versionado: Git, ZIP, manifest e secret scan;
- evidência runtime: data/reports, readiness, container snapshot, reports paper/shadow e hashes.

O bundle é gerado fora do Git em data/evidence_packs/ e não deve ser versionado.

## Regra operacional

O sidecar é read-only em relação a trading:

- paper_only=true
- shadow_only=true
- live_trading_enabled=false
- live_release_allowed=false
- canary_release_allowed=false
- order_submission_enabled=false
- real_order_submission_enabled=false
- exchange_private_access=false
- sends_orders=false
- changes_risk=false
- changes_training_dataset=false
- writes_trades_master=false

## Comando

python scripts/build_runtime_evidence_sidecar_bundle.py --json --include-containers

## Saídas

data/evidence_packs/runtime_evidence_sidecar_YYYYMMDD_HHMMSSZ/
  MANIFEST.json
  SHA256SUMS.txt
  validation_summary.json
  sources/

## Interpretação

sidecar.status=ok significa que o pacote de evidências foi criado com segurança e sem flags perigosas.

Isso não libera live/canary. O readiness pode continuar blocked, especialmente por:

- soak_days_below_required
- readiness_gate_blocked
- monte_carlo_blocked
- paper_soak_report_source_status_blocked

## Fonte ausente opcional

A ausência de runtime_manual_notification_test_dispatch_report não bloqueia o sidecar. Esse report é evidência manual opcional; a validação runtime crítica permanece dependente de runtime_observability_status=ok.

## Definition of Done

- Manifesto do projeto atual.
- Secret scan sem achados.
- Runtime observability ok.
- Container snapshot ok quando solicitado.
- Sidecar com MANIFEST.json.
- Sidecar com SHA256SUMS.txt.
- Nenhuma flag de ordem, live, risco, private exchange ou escrita em dataset/trades.
