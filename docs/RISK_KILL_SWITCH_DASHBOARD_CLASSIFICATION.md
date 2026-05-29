# Risk Kill Switch Dashboard Classification

Este documento define a classificação institucional do kill switch para o FUTUROS/SmartCrypto.

## Problema Operacional

Um JSON de kill switch com `enabled=true` pode representar coisas diferentes:

- bloqueio ativo;
- bloqueio histórico;
- bloqueio expirado;
- arquivo ausente;
- arquivo inválido/corrompido;
- impacto real no paper;
- impacto apenas em live futuro.

Sem classificação explícita, o operador pode interpretar errado se o paper está realmente bloqueado.

## Estados

| Status | Rótulo Dashboard | Significado |
| --- | --- | --- |
| `missing` | AUSENTE | Arquivo não existe. Não bloqueia por si só. |
| `inactive` | INATIVO | Arquivo existe e `enabled=false`. |
| `active` | ATIVO | `enabled=true` sem expiração ou com `expires_at` futuro. |
| `expired` | EXPIRADO | `enabled=true`, mas `expires_at` está no passado. |
| `historical` | HISTÓRICO | Estado antigo preservado para auditoria, sem bloqueio atual. |
| `invalid` | INVÁLIDO | Arquivo não pôde ser parseado ou possui campos inválidos. |

## Diferença Entre Active, Expired E Historical

- `active`: bloqueio vigente agora.
- `expired`: bloqueio já venceu por `expires_at` e não deve bloquear paper quando a política de expiração é aplicável.
- `historical`: informação preservada para auditoria, não autoridade de bloqueio atual.

## Impacto Em Paper

O classificador expõe `blocks_paper`.

- `active`: `blocks_paper=true`.
- `invalid`: `blocks_paper=true` por comportamento conservador.
- `missing`, `inactive`, `expired`, `historical`: `blocks_paper=false`.

## Impacto Em Live Futuro

O projeto permanece paper/shadow only com live trading bloqueado. Ainda assim, o classificador expõe `blocks_live` para deixar claro como o estado deveria ser tratado caso exista um live canary futuro:

- `active`: `blocks_live=true`.
- `invalid`: `blocks_live=true`.
- `missing`, `inactive`, `expired`, `historical`: `blocks_live=false`.

## Arquivo Inválido

Arquivo inválido é tratado de forma conservadora:

- `status=invalid`;
- `active_now=true`;
- `blocks_live=true`;
- `blocks_paper=true`, conforme política atual;
- `parse_error` preenchido para auditoria.

## Dashboard

O dashboard usa leitura read-only:

- não cria kill switch;
- não ativa/desativa kill switch;
- não altera arquivo runtime;
- não envia ordem;
- não acessa exchange privada.

Campos expostos:

- `status`;
- `active_now`;
- `blocks_paper`;
- `blocks_live`;
- `reason`;
- `created_at`;
- `expires_at`;
- `age_minutes`;
- `source_path`;
- `parse_error`.

## Segurança

Esta classificação não altera Docker, `.env`, `START_PAPER_24H`, strategy Freqtrade, Qlib, IA Shadow, Fase 5 ou Fase 14.

O projeto continua paper/shadow only, com live trading bloqueado, ordem real bloqueada e leitura privada proibida.

## Validação

```powershell
python -m compileall smartcrypto scripts tests
python -m pytest tests/test_kill_switch_dashboard_classification.py
python -m pytest
```
