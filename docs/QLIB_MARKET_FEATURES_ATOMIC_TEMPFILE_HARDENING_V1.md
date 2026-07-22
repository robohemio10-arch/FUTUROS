# Qlib Market Features Atomic Tempfile Hardening V1

## Objetivo

Eliminar a disputa sobre o temporário determinístico:

    data/features/market_features_60d.parquet.tmp

A alteração preserva a semântica do pipeline Qlib paper e seu contrato
operacional sem lookahead.

## Incidente

O primeiro ciclo do qlib-refresh-supervisor-paper falhou ao abrir o arquivo
temporário determinístico. O bootstrap de permissões havia concluído e o
processo já executava como UID/GID 10001:10001.

Após o restart automático, o ciclo seguinte concluiu com status ok.

## Causa estrutural

O writer anterior utilizava sempre o mesmo nome temporário:

    target.with_suffix(target.suffix + ".tmp")

Esse desenho permitia disputa com resíduos anteriores, writers concorrentes
ou locks transitórios do bind mount.

Os testes também demonstraram uma segunda fronteira: mesmo com temporários
exclusivos, promoções simultâneas por os.replace sobre o mesmo destino podem
produzir WinError 5 no Windows.

## Solução

O writer agora:

1. cria um temporário exclusivo com tempfile.mkstemp;
2. cria esse temporário no mesmo diretório do destino;
3. mantém a serialização Parquet concorrente;
4. serializa somente a promoção final dentro do processo;
5. promove o resultado com os.replace;
6. aplica retry exponencial curto e limitado para locks transitórios;
7. falha imediatamente para erros permanentes;
8. remove somente o temporário pertencente à execução;
9. preserva o arquivo final anterior em caso de falha;
10. não remove nem reutiliza o .tmp determinístico legado.

## Retry

São executadas no máximo cinco tentativas.

O intervalo inicial é de 50 milissegundos, com crescimento exponencial.

São classificados como transitórios:

- PermissionError;
- EACCES;
- EPERM;
- EBUSY;
- Windows error 5;
- Windows sharing violation 32;
- Windows lock violation 33.

## Segurança

Esta alteração não modifica:

- Docker Compose;
- bootstrap de permissões;
- UID ou GID;
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
