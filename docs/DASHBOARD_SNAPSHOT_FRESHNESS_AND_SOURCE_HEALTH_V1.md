# Dashboard Snapshot Freshness And Source Health V1

## Objetivo

Esta branch fortalece o closeout de fontes do SMART FUTUROS Institutional Dashboard. O closeout anterior identifica a relação entre página, snapshot, fonte e bloqueio. Esta camada acrescenta a evidência temporal: qual timestamp foi usado, de onde veio, qual política foi aplicada e por que a fonte ficou saudável, degradada ou bloqueada.

O dashboard permanece snapshot-first, read-only e paper/shadow only. A avaliação não executa produtores e não modifica arquivos runtime.

## Política De Freshness

Cada fonte recebe uma política explícita, inclusive quando freshness não se aplica. Os valores de `freshness_basis` são:

- `PAYLOAD_TIMESTAMP`: exige timestamp válido no payload.
- `FILE_MTIME`: usa somente o mtime do arquivo.
- `PAYLOAD_TIMESTAMP_OR_FILE_MTIME`: prefere o payload e usa mtime apenas quando o campo está ausente e o fallback foi autorizado.
- `NOT_APPLICABLE`: não calcula idade.

Os campos aceitos incluem `last_updated_utc`, `generated_at_utc`, timestamps do build e campos de tempo de dados de mercado. Todos são normalizados para UTC.

Um campo presente e inválido nunca é substituído silenciosamente pelo mtime. Ele produz `INVALID_TIMESTAMP`, freshness `UNKNOWN` e saúde bloqueada ou degradada conforme o nível da fonte. O fallback para mtime só ocorre quando nenhum campo aceito está preenchido e a política permite `fallback_to_mtime`.

As políticas configuradas podem declarar:

- `warning_age_seconds`: inicia `WARNING_STALE`;
- `critical_age_seconds`: inicia `CRITICAL_STALE`;
- `max_age_seconds`: limite institucional preservado por compatibilidade;
- comportamento para stale, missing e timestamp inválido;
- producer, operator e runbook hints.

## Source Health

Os estados de saúde são:

- `HEALTHY`: fonte válida e fresh, ou artefato gerado pelo build atual.
- `DEGRADED`: problema opcional ou warning de freshness sem bloqueio global.
- `BLOCKED`: fonte required ausente, inválida, sem timestamp obrigatório ou criticamente stale.
- `PLANNED`: fonte `FUTURE_SOURCE_PENDING`, sem falso erro operacional.
- `UNKNOWN`: informação insuficiente para classificação segura.

Os estados de freshness são:

- `FRESH`
- `WARNING_STALE`
- `CRITICAL_STALE`
- `STALE`
- `NOT_APPLICABLE`
- `UNKNOWN`

Cada linha da matriz registra o timestamp efetivo, sua origem (`payload`, `file_mtime`, `unavailable` ou `not_applicable`), mtime, idade, limites, páginas e snapshots consumidores, bloqueios e remediação recomendada.

## Níveis De Fonte

- `REQUIRED`: missing, conteúdo inválido ou critical stale bloqueia páginas consumidoras e readiness.
- `OPTIONAL`: falhas degradam observabilidade sem bloquear globalmente.
- `FUTURE_SOURCE_PENDING`: aparece como `PLANNED`; não autoriza nem bloqueia operação atual.
- `GENERATED`: representa snapshot produzido pelo builder do dashboard, sem autoridade operacional.

## Consistência Global

O build summary preserva `source_matrix`, `page_source_matrix`, `dashboard_status` e `global_blocking_reasons`, e acrescenta `source_health_matrix`, contagens por health/freshness, fontes stale por nível, timestamps inválidos, cobertura de política e `global_source_health_status`.

O snapshot global recebe as mesmas matrizes e razões. Uma página com blocking source não pode ser `OK`, e o dashboard global não pode ser `OK` enquanto existir bloqueio global.

As páginas 01 e 06 apenas exibem a matriz consolidada. A tabela mostra saúde, freshness, timestamp efetivo, origem, política, limite e ação recomendada; ela não executa a ação.

## Validação

```powershell
python -m compileall -q scripts smartcrypto tests
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root . --json
python scripts/build_dashboard_snapshots.py --project-root . --output-dir data/reports --strict false --once --json
$DashboardTests = Get-ChildItem ".\tests" -Filter "test_dashboard_*.py" | ForEach-Object { $_.FullName }
python -m pytest $DashboardTests -q
python -m pytest tests/test_dashboard_snapshot_freshness_source_health_v1.py -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

## Segurança

Esta camada não usa exchange, endpoints privados, `ccxt`, OrderManager, ExchangeGateway, Telegram, NTFY ou HTTP de alerta. Não executa OCR, Bitradex, rebuild de dataset, treino ou promoção de modelo. Não altera Qlib, IA Shadow, RiskManager, active signals, readiness gates, live, canary ou order submission.

## Fora De Escopo

- renovar ou corrigir fontes runtime;
- alterar producers ou seus schemas;
- criar fontes futuras;
- modificar limites operacionais para esconder staleness;
- qualquer ação de trading, risco, modelo, dataset ou notificação.
