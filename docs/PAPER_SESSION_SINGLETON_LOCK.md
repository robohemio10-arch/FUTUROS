# Paper Session Singleton Lock

Este documento descreve a trava institucional para impedir execução duplicada de sessões paper no FUTUROS/SmartCrypto.

## Objetivo

Impedir que duas sessões paper sejam iniciadas ao mesmo tempo, especialmente dois `START_PAPER_24H.ps1` ou `START_PAPER_7D.ps1` simultâneos.

O projeto permanece paper/shadow only:

- live trading bloqueado;
- envio de ordem real bloqueado;
- leitura privada de conta proibida;
- Freqtrade permanece somente dry-run/paper.

## Lock Runtime

O lock fica em:

```text
data/runtime/paper_session.lock
```

O arquivo é runtime state e não deve ser versionado.

O JSON contém:

- `pid`;
- `started_at`;
- `script`;
- `project_root`;
- `mode`.

## Regras

- Se não existir lock, o start cria o lock e continua.
- Se existir lock com processo vivo, o start aborta com erro claro.
- Se existir lock com processo morto, o lock stale é removido e o start continua.
- O release normal só remove o lock se `pid` e `script` corresponderem.
- O `STOP_PAPER_SESSION.ps1` faz cleanup seguro: remove apenas lock stale.
- Monitor e dashboard não criam lock.

## Integração

`START_PAPER_24H.ps1` chama:

```powershell
python -m smartcrypto.runtime.paper_session_lock acquire
```

`START_PAPER_7D.ps1` delega para o 24H com `-SessionHours 168`, portanto usa a mesma proteção.

`STOP_PAPER_SESSION.ps1` chama:

```powershell
python -m smartcrypto.runtime.paper_session_lock release --cleanup-stale
```

## Inspeção Manual

```powershell
python -m smartcrypto.runtime.paper_session_lock inspect
```

Status possíveis:

- `clear`: nenhum lock;
- `active`: lock aponta para PID vivo;
- `stale`: lock existe, mas o PID não está vivo;
- `blocked`: tentativa insegura de acquire/release.

## Segurança

A trava não acessa exchange, não lê API privada, não altera Docker, não altera `.env`, não altera strategy, não altera Qlib, não altera IA Shadow, não altera Fase 5 e não altera Fase 14.

Ela apenas controla exclusividade local de sessão paper.
