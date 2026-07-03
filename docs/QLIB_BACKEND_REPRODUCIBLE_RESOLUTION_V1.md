# QLIB Backend Reproducible Resolution V1

## Objetivo

Resolver a primeira etapa institucional da Branch `codex/qlib-backend-reproducible-resolution-v1`: deixar o backend Qlib declarado, pinado em lockfile dedicado de research e auditável pelos gates existentes, sem incluir Qlib no runtime de execução paper/live.

## Escopo implementado

- `pyproject.toml` passa a declarar o extra Qlib com versão exata: `pyqlib==0.9.7`.
- `requirements-qlib.lock` foi criado como lockfile dedicado de research/training.
- O auditor `qlib_research_backend_environment_lock_v1` reconhece `requirements-qlib.lock`.
- O gate `qlib_research_backend_runtime_dependency_gate_v1` usa a mesma semântica institucional do auditor de lock.
- `qlib_dependency_pinned=true` somente quando existe `pyqlib==X.Y.Z` ou `qlib==X.Y.Z` em lockfile versionado.
- Pyproject, mesmo com versão exata, é tratado como declaração, não como lock.
- Safety flags permanecem bloqueando live, canary, ordem real, registry ativo, promoção de modelo, alteração de runtime e exchange privada.

## Arquivos alterados/criados

- `requirements-qlib.lock`
- `pyproject.toml`
- `smartcrypto/learning/qlib_backend_environment_lock/environment_lock.py`
- `smartcrypto/learning/qlib_backend_gate/environment_audit.py`
- `smartcrypto/learning/qlib_backend_gate/dependency_contract.py`
- `tests/test_qlib_research_backend_environment_lock_v1.py`
- `tests/test_qlib_research_backend_gate_v1.py`
- `docs/QLIB_BACKEND_REPRODUCIBLE_RESOLUTION_V1.md`

## Estados auditáveis

### `declared_not_locked`

Existe declaração Qlib no `pyproject.toml`, mas não existe entrada exata em lockfile versionado.

### `locked_not_importable`

Existe lockfile versionado com `pyqlib==X.Y.Z`, mas o pacote ainda não está importável no ambiente corrente.

### `locked`

Existe lockfile versionado e o backend Qlib está importável com os módulos obrigatórios.

### `locked_with_documented_backend_blocker`

Existe lockfile versionado, mas a probe do backend foi bloqueada por incompatibilidade ou isolamento de runtime.

## Lockfile dedicado

O Qlib é dependência de pesquisa e treinamento. Ele não deve entrar no runtime do executor paper/live.

```text
requirements-qlib.lock
```

Conteúdo inicial:

```text
pyqlib==0.9.7
```

## Comandos operacionais

Instalar o backend Qlib no ambiente de research:

```powershell
python -m pip install -r .\requirements-qlib.lock
```

Validar imports reais:

```powershell
python -c "import qlib; print(qlib.__version__)"
python -c "import qlib.data; import qlib.workflow; import qlib.contrib; import qlib.contrib.model; print('qlib imports ok')"
```

Auditar ambiente:

```powershell
python .\scripts\audit_qlib_research_backend_environment_lock_v1.py --project-root . --json
python .\scripts\audit_qlib_research_backend_gate_v1.py --project-root . --json
```

Validações focadas:

```powershell
python -m compileall scripts smartcrypto tests
python -m pytest .\tests\test_qlib_research_backend_environment_lock_v1.py -q
python -m pytest .\tests\test_qlib_research_backend_gate_v1.py -q
python .\scripts\generate_project_manifest.py --check
python .\scripts\scan_versioned_secrets.py --project-root . --json
```

## Safety contract

Esta branch não autoriza:

- live trading;
- canary real;
- ordem real;
- exchange privada;
- alteração automática de risco;
- alteração de RiskManager;
- alteração de Freqtrade para live;
- promoção de modelo;
- registry ativo;
- alteração de runtime.

Os auditores devem continuar retornando:

```json
{
  "paper_only": true,
  "shadow_only": true,
  "live_release_allowed": false,
  "canary_release_allowed": false,
  "order_submission_enabled": false,
  "real_order_submission_enabled": false,
  "sends_orders": false,
  "exchange_private_access": false,
  "model_promotion_performed": false,
  "registry_write_performed": false,
  "active_model_changed": false,
  "qlib_runtime_updated": false
}
```

## Resultado esperado antes da instalação

Em ambiente onde `pyqlib` ainda não foi instalado:

```json
{
  "qlib_dependency_declared": true,
  "qlib_dependency_pinned": true,
  "environment_lock_status": "locked_not_importable",
  "qlib_backend_status": "unavailable",
  "qlib_importable": false
}
```

## Resultado esperado após instalação do lock

Após `python -m pip install -r .\requirements-qlib.lock`:

```json
{
  "qlib_dependency_declared": true,
  "qlib_dependency_pinned": true,
  "environment_lock_status": "locked",
  "qlib_backend_status": "available",
  "qlib_importable": true
}
```

## Trainer backend gate freshness

The Qlib institutional ranking trainer must not trust a default `data/reports/qlib_research_backend_gate_v1.json` as authoritative when the caller did not explicitly provide `--backend-gate-report`.

Reason: the backend audit CLIs are read-only by default. Running `audit_qlib_research_backend_gate_v1.py --json` prints current availability but does not overwrite `data/reports/qlib_research_backend_gate_v1.json` unless `--write` is supplied. A stale report can therefore keep reporting `qlib_backend_status=unavailable` even after `pyqlib==0.9.7` is installed and importable.

Branch 53 fixes this by making the trainer perform a live no-write backend gate probe by default. Explicit `--backend-gate-report` still remains honored as immutable evidence input.

Expected behavior:

```text
no --backend-gate-report -> backend_gate_report_status=live_probe
explicit --backend-gate-report -> backend_gate_report_status=provided
```

Safety remains unchanged: no model promotion, no registry write, no runtime update, no order submission, no exchange private access.

## Trainer training flag consistency

When explicit research training succeeds with the Qlib backend available, the trainer report must keep top-level flags and nested `safety_flags` aligned:

```json
{
  "training_requested": true,
  "qlib_challenger_training_performed": true,
  "qlib_training_performed": true,
  "safety_flags": {
    "training_requested": true,
    "qlib_challenger_training_performed": true,
    "qlib_training_performed": true,
    "sends_orders": false,
    "exchange_private_access": false,
    "registry_write_performed": false,
    "model_promotion_performed": false,
    "qlib_runtime_updated": false
  }
}
```

These flags describe research-only challenger training evidence. They do not authorize runtime activation, registry promotion, order submission, private exchange access, live trading, or canary release.
