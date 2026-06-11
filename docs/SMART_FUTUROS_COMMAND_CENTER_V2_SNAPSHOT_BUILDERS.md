# SMART FUTUROS Command Center v2: snapshot builders

## Objetivo

Esta segunda etapa materializa snapshots JSON read-only para o SMART FUTUROS Command
Center. Ela conecta os contratos fundacionais da Branch 1 as futuras paginas Streamlit,
sem conceder autoridade operacional ao dashboard.

O nome oficial do produto e SMART FUTUROS. A interface e SMART FUTUROS Command Center,
e a denominacao tecnica e SMART FUTUROS Institutional Dashboard.

## Arquitetura snapshot-first

```text
fontes operacionais e relatorios autorizados
  -> builders backend read-only
  -> data/reports/dashboard_*_snapshot.json
  -> paginas Streamlit read-only da Branch 3
```

Contratos definem schema e safety. Builders leem fontes e calculam metricas. Paginas
Streamlit apenas consumirao os snapshots; elas nao chamam builders nem fontes brutas.

## Oito builders

1. `infrastructure_snapshot_builder.py`
2. `portfolio_risk_snapshot_builder.py`
3. `grid_monitor_snapshot_builder.py`
4. `opportunity_scanner_snapshot_builder.py`
5. `ai_governance_snapshot_builder.py`
6. `active_controls_snapshot_builder.py`
7. `quantitative_reports_snapshot_builder.py`
8. `alerts_messaging_snapshot_builder.py`

Cada builder usa o source catalog, loader read-only, safe math, build context e status
helpers da Branch 1. O retorno e um dicionario JSON serializavel; somente o orquestrador
global escreve os arquivos.

## Dez snapshots gerados

- `dashboard_global_status_snapshot.json`
- `dashboard_infrastructure_snapshot.json`
- `dashboard_portfolio_risk_snapshot.json`
- `dashboard_grid_monitor_snapshot.json`
- `dashboard_opportunity_scanner_snapshot.json`
- `dashboard_ai_governance_snapshot.json`
- `dashboard_active_controls_snapshot.json`
- `dashboard_quantitative_reports_snapshot.json`
- `dashboard_alerts_messaging_snapshot.json`
- `dashboard_snapshot_build_summary.json`

A Aba 8 usa exclusivamente `dashboard_alerts_messaging_snapshot.json`.

## Output-dir autorizado

O script aceita um unico `output-dir`. Somente os dez nomes canonicos podem ser gravados
dentro desse diretorio. A escrita usa arquivo temporario no mesmo diretorio e substituicao
atomica. Nomes com traversal ou fora da lista sao rejeitados.

O default e `data/reports`. Esse diretorio contem runtime efemero: nao entra no manifesto,
nao deve ser adicionado ao Git e pode ser apagado e reconstruido sem impacto no codigo.

## Strict false e strict true

Com `--strict false`, fontes required ausentes aparecem em cada snapshot e no summary,
mas a rodada termina com exit code 0. Fontes optional e future nunca interrompem o build.

Com `--strict true`, fontes required ausentes ainda produzem snapshots diagnosticos, mas
o summary fica `blocked` e o processo retorna exit code 2. Erros estruturais retornam 3,
sempre com JSON controlado, sem traceback como contrato de saida.

Estados de fonte:

- required ausente: `MISSING_REQUIRED`;
- optional ausente: `MISSING_OPTIONAL`;
- future ausente: `UNKNOWN` e `future_sources_pending`;
- artefato gerado pelo dashboard: nunca e tratado como input.

## Execucao

```powershell
python .\scripts\build_dashboard_snapshots.py --once --strict false --output-dir data/reports
```

Para um gate institucional estrito:

```powershell
python .\scripts\build_dashboard_snapshots.py --once --strict true --output-dir data/reports --json
```

## Validacao

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest tests/test_dashboard_build_snapshots_script_v2.py -q
python -m pytest tests/test_dashboard_infrastructure_snapshot_builder_v2.py tests/test_dashboard_portfolio_risk_snapshot_builder_v2.py tests/test_dashboard_grid_monitor_snapshot_builder_v2.py tests/test_dashboard_opportunity_scanner_snapshot_builder_v2.py tests/test_dashboard_ai_governance_snapshot_builder_v2.py tests/test_dashboard_active_controls_snapshot_builder_v2.py tests/test_dashboard_quantitative_reports_snapshot_builder_v2.py tests/test_dashboard_alerts_messaging_snapshot_builder_v2.py -q
python -m pytest -q
python scripts/generate_project_manifest.py --check
python scripts/scan_versioned_secrets.py --json
```

## Safety

Todos os snapshots declaram paper/shadow only, live locked, order submission disabled,
private exchange disabled e auditoria read-only. Builders nao acessam exchange, nao leem
segredos, nao escrevem banco operacional, nao alteram risco, nao modificam modelos ou
active signals e nao enviam notificacoes.

Esta branch nao cria paginas Streamlit, nao executa ordens, nao altera risco, nao chama
exchange, nao envia notificacoes e nao promove modelos.

## Proxima etapa

A Branch 3 criara as paginas Streamlit read-only. Sua unica fonte institucional sera a
familia `dashboard_*_snapshot.json`; toda logica de agregacao permanece neste backend.
