# Qlib Market Features Atomic Tempfile Hardening V1

## Objetivo

Eliminar a disputa sobre o temporário determinístico:

    data/features/market_features_60d.parquet.tmp

A alteração preserva a semântica do pipeline Qlib paper e seu contrato
operacional sem lookahead.

## Incidente inicial

O primeiro ciclo do `qlib-refresh-supervisor-paper` falhou ao abrir o arquivo
temporário determinístico. O bootstrap de permissões havia concluído e o
processo já executava como UID/GID `10001:10001`.

Após o restart automático, o ciclo seguinte concluiu com `status=ok`.

## Primeira causa estrutural

O writer anterior utilizava sempre o mesmo nome temporário:

    target.with_suffix(target.suffix + ".tmp")

Esse desenho permitia disputa com resíduos anteriores, writers concorrentes
ou locks transitórios do bind mount.

Os testes também demonstraram uma segunda fronteira: mesmo com temporários
exclusivos, promoções simultâneas por `os.replace` sobre o mesmo destino podem
produzir erro transitório no Windows.

## Solução incorporada pelo PR #321

O writer passou a:

1. criar um temporário exclusivo com `tempfile.mkstemp`;
2. criar esse temporário no mesmo diretório do destino;
3. manter a serialização Parquet concorrente;
4. serializar somente a promoção final dentro do processo;
5. promover o resultado com `os.replace`;
6. aplicar retry exponencial curto e limitado à promoção;
7. falhar imediatamente para erros permanentes;
8. remover somente o temporário pertencente à execução;
9. preservar o arquivo final anterior em caso de falha;
10. não remover nem reutilizar o `.tmp` determinístico legado.

## Evidência posterior ao PR #321

Um novo cold start falhou antes da serialização e antes da promoção:

    PermissionError: [Errno 13] Permission denied:
    /app/data/features/.market_features_60d.parquet.<token>.tmp

A falha ocorreu dentro de `tempfile.mkstemp`. Portanto, o temporário exclusivo
eliminou a colisão nominal, mas a criação do próprio temporário ainda precisava
de tolerância limitada a indisponibilidade transitória do bind mount.

## Hardening complementar

A criação do temporário agora possui retry próprio:

- até oito tentativas;
- atraso inicial de 50 milissegundos;
- backoff exponencial;
- nomes exclusivos a cada tentativa;
- retry apenas para erros transitórios;
- falha imediata para erros permanentes;
- nenhuma remoção de arquivos alheios;
- preservação do arquivo final anterior.

A promoção final permanece com cinco tentativas e lock process-local.

## Classificação de erros transitórios

São classificados como transitórios:

- `PermissionError`;
- `EACCES`;
- `EPERM`;
- `EBUSY`;
- Windows error 5;
- Windows sharing violation 32;
- Windows lock violation 33.

`ENOSPC` e demais erros permanentes permanecem fail-closed.

## Segurança

Esta alteração não modifica:

- Docker Compose;
- UID ou GID operacionais;
- Freqtrade;
- RiskManager;
- modelos;
- Decision Ledger;
- autolearning;
- notificações;
- live;
- canary;
- envio de ordens;
- acesso privado à exchange.
