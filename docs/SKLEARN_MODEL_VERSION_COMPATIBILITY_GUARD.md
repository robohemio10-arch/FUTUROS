# Sklearn Model Version Compatibility Guard

Este guard registra compatibilidade de versão sklearn para artefatos carregados pelo pipeline Qlib.

## Problema

Modelos e encoders salvos com `pickle`/`joblib` podem depender da versão exata do scikit-learn usada no treino/exportação. O warning observado no runtime foi:

```text
InconsistentVersionWarning:
Trying to unpickle estimator LabelEncoder from version 1.8.0 when using version 1.7.0
```

Mesmo em paper/shadow, isso é risco operacional: uma predição pode continuar rodando, mas com comportamento diferente do ambiente de treino.

## O Que O Guard Faz

O módulo `smartcrypto/qlib_engine/sklearn_compatibility.py`:

- detecta a versão sklearn em runtime;
- tenta detectar a versão sklearn salva no artefato quando há metadata;
- captura `InconsistentVersionWarning` explicitamente durante `joblib.load`;
- registra status no JSON do runner:
  - `ok`;
  - `warning`;
  - `incompatible`;
  - `unknown`.

O relatório do `run_qlib_fresh_predictions.py` expõe:

- `sklearn_runtime_version`;
- `sklearn_artifact_version`;
- `sklearn_compatibility_status`;
- `sklearn_compatibility_reason`;
- `sklearn_compatibility`.

## Modo Permissivo

O modo padrão é permissivo. Se houver warning ou versão diferente, o runner mantém `status=ok` quando o restante do pipeline está válido, mas marca:

```json
"sklearn_compatibility_status": "warning"
```

Isso evita quebrar o paper/shadow atual por um warning não bloqueante, mas torna o risco auditável.

## Modo Strict

O modo strict pode ser acionado no futuro com:

```powershell
python .\scripts\run_qlib_fresh_predictions.py --sklearn-strict-compatibility
```

Se o artefato estiver claramente incompatível, o runner retorna:

```json
{
  "status": "blocked",
  "reason": "sklearn_artifact_incompatible"
}
```

## O Que Esta Branch Nao Faz

Esta branch não retreina modelo, não reexporta encoder e não altera Docker, `.env`, `START_PAPER_24H`, strategy, IA Shadow, Fase 5 ou Fase 14.

Também não acessa exchange privada, não envia ordem e mantém o projeto paper/shadow only com live trading bloqueado.

## Próxima Etapa Recomendada

Retreinar/reexportar os artefatos Qlib com versão sklearn pinada e registrar no payload do modelo:

```json
"sklearn_artifact_version": "<versao_do_treino>"
```

Depois disso, o modo strict pode ser promovido de diagnóstico para bloqueio obrigatório.
