# SMART FUTUROS Dashboard Semantic Coverage Audit V2

## Objetivo

Esta micro-branch fecha a fase SMART FUTUROS Command Center com uma auditoria semântica read-only.

Ela verifica se o dashboard pós-Branch 05 cobre os contratos funcionais, visuais e de governança prometidos nas cinco branches do dashboard.

## Escopo

A auditoria cobre:

- oito páginas oficiais do SMART FUTUROS Command Center;
- snapshots canônicos de cada página;
- shell Streamlit com marca SMART FUTUROS;
- tema visual institucional local;
- stubs de controles N1/N2/N3;
- N4 HARD_BLOCKED;
- alertas e mensageria em stub;
- Readiness & Gates na Aba 6;
- Financial Event Log / Decision Trace na Aba 7;
- Dataset / OCR / Training Pipeline Status na Aba 7;
- ausência de nona aba;
- ausência de termo externo histórico na UI/código novo;
- ausência do nome deprecated do snapshot de alertas;
- CSS local-only, sem CDN, URL externa ou import remoto.

## Arquivos

- `smartcrypto/ops/dashboard_semantic_audit/contracts.py`
- `smartcrypto/ops/dashboard_semantic_audit/catalog.py`
- `smartcrypto/ops/dashboard_semantic_audit/auditor.py`
- `scripts/audit_dashboard_semantic_coverage_v2.py`
- `tests/test_dashboard_semantic_coverage_contracts_v2.py`
- `tests/test_dashboard_semantic_coverage_auditor_v2.py`
- `tests/test_dashboard_semantic_coverage_script_v2.py`
- `tests/test_dashboard_semantic_coverage_static_safety_v2.py`

## Execução

Resumo:

```powershell
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root .
```

Relatório completo em JSON:

```powershell
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root . --json
```

Somente resumo:

```powershell
python scripts/audit_dashboard_semantic_coverage_v2.py --project-root . --summary-only
```

## Segurança operacional

Esta auditoria não executa comandos e não altera runtime.

Ela não:

- envia ordens;
- habilita live;
- habilita canary;
- acessa exchange privada;
- envia Telegram ou NTFY;
- usa HTTP;
- lê tokens;
- altera risco;
- altera modelo;
- altera active signals;
- executa OCR;
- importa trades;
- reconstrói datasets;
- limpa SQLite;
- altera readiness;
- escreve snapshots;
- escreve arquivos runtime.

## Critérios de aprovação

A micro-branch está aprovada quando:

- `audit_dashboard_semantic_coverage_v2.py` retorna `status=ok`;
- todos os testes semantic coverage passam;
- testes do tema visual continuam passando;
- testes de controles/alertas continuam passando;
- manifesto fica current;
- secret scan permanece OK;
- não há artefato runtime versionado;
- `git status --short` fica limpo após commit.

## Próxima etapa

Após esta micro-branch, a próxima frente recomendada é:

```text
codex/paper-shadow-soak-anchor-and-continuity-pack
```
