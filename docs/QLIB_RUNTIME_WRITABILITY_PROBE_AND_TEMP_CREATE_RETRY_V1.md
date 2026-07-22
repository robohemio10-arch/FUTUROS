# Qlib Runtime Writability Probe and Temp Create Retry V1

## Objetivo

Fechar a lacuna entre a preparação administrativa das permissões e a
capacidade real de escrita do processo non-root no bind mount paper.

A implementação adiciona duas camadas complementares:

1. probe de escrita após a queda de privilégios e antes do `exec`;
2. retry limitado na criação do temporário atômico de market features.

## Evidência operacional

No reinício controlado do core paper, o supervisor Qlib:

- concluiu o bootstrap de ownership e mode;
- baixou para UID/GID `10001:10001`;
- falhou em `tempfile.mkstemp`;
- reiniciou uma vez;
- acionou contenção dos sete serviços.

A falha ocorreu em:

    /app/data/features/.market_features_60d.parquet.<token>.tmp

Isso exclui colisão do nome temporário determinístico como causa completa.
O diretório ficou indisponível para criação no instante inicial do cold start.

## Probe pós-drop

Depois de `setgid`, `setuid` e validação da identidade efetiva, cada diretório
do perfil selecionado é testado pelo próprio usuário operacional.

Para cada diretório autorizado, o probe executa:

1. criação exclusiva de arquivo com nome aleatório;
2. escrita de payload mínimo;
3. `flush`;
4. `fsync`;
5. fechamento;
6. remoção do próprio arquivo.

A aplicação somente é executada depois que todos os diretórios passam.

## Retry do probe

O probe utiliza:

- oito tentativas;
- atraso inicial de 50 milissegundos;
- backoff exponencial;
- retry somente para erros transitórios;
- fail-closed após esgotamento;
- cleanup restrito ao arquivo do próprio probe.

Erros permanentes, como `ENOSPC`, não são repetidos.

## Evento de evidência

O bootstrap emite:

    runtime_writability_verified

O evento contém:

- `directory_count`;
- `probe_attempt_count`;
- `probe_retry_count`;
- todas as safety flags paper/shadow já existentes.

Quando o probe falha, `exec_application` não é chamado e o bootstrap retorna
código controlado 2 com evento `runtime_permissions_bootstrap_blocked`.

## Retry do writer

`smartcrypto/market/market_feature_schema.py` passa a repetir também a criação
do temporário exclusivo. A promoção final continua separada e protegida por:

- lock process-local;
- `os.replace`;
- cinco tentativas;
- classificação compartilhada de erros transitórios.

## Escopo

Arquivos funcionais:

- `scripts/docker_runtime_permissions_bootstrap.py`;
- `smartcrypto/market/market_feature_schema.py`.

Cobertura:

- retry de `PermissionError` e `EBUSY`;
- ausência de retry para `ENOSPC`;
- esgotamento fail-closed;
- cleanup apenas do temporário próprio;
- preservação de arquivos alheios;
- probe real de write/flush/fsync/close/unlink;
- bloqueio do `exec` quando o probe falha;
- ordem `drop privileges -> probe -> exec`;
- contrato paper-only e Decision Ledger desabilitado.

## Limites

A implementação não:

- altera `docker-compose.paper.yml`;
- amplia a allowlist de paths;
- autoriza `/app/data` genericamente;
- executa shell;
- usa `chmod 777`;
- altera Freqtrade ou RiskManager;
- ativa autolearning;
- ativa notificações reais;
- ativa Decision Ledger;
- habilita live, canary ou ordens;
- inicia ou reinicia containers durante a implementação.
