# Phase 14 Relative Output Atomic Tempfile Fix V1

## Objetivo

Esta branch corrige exclusivamente a normalizacao de paths do exporter do snapshot
SQLite do Phase 14. Ela nao inicia containers, nao reinicia paper e nao constitui
certificacao runtime.

## Incidente V5

O cold-start controlado V5 foi contido depois de o servico
`phase14-feedback-sync-paper` permanecer unhealthy. O bootstrap havia concluido:

```text
runtime_bootstrap_lock_acquired
runtime_permissions_prepared
runtime_privileges_dropped
runtime_writability_verified
runtime_bootstrap_lock_released
```

O processo operava com UID/GID 10001 e passou os probes de writability. A falha
subsequente foi controlada:

```text
snapshot_status=blocked
snapshot_reason=snapshot_tempfile_creation_failed
```

Nao houve evidencia de falha de permissao nesse ciclo. O Qlib permaneceu healthy,
o bot nao foi criado e a contencao encerrou os servicos core.

## Causa raiz

O runtime fornece um output logico relativo a `/app`:

```text
data/snapshots/freqtrade-paper/tradesv3.paper.snapshot.sqlite
```

`tempfile.mkstemp` devolve um path absoluto. A implementacao anterior comparava
diretamente o parent absoluto do tempfile com o parent relativo do target. Assim,
um tempfile corretamente criado em `/app/data/snapshots/freqtrade-paper` era
rejeitado.

Classificacao:

```text
ROOT_CAUSE=relative_target_parent_compared_with_absolute_mkstemp_parent
FILESYSTEM_PERMISSION_FAILURE=false
BOOTSTRAP_PERMISSION_FAILURE=false
ATOMIC_EXPORTER_PATH_NORMALIZATION_DEFECT=true
```

## Modelo de paths

`SnapshotPaths` separa os valores publicos dos paths usados pelo filesystem:

- `logical_source` e `logical_target` preservam exatamente as entradas do caller;
- `filesystem_source`, `filesystem_target` e
  `filesystem_target_parent` sao absolutos e normalizados;
- paths relativos sao resolvidos contra `working_directory`;
- o default de `working_directory` permanece `Path.cwd()`.

Os campos `source` e `output` do report continuam logicos. A normalizacao interna
nao converte silenciosamente um output relativo em `/app/...`.

## Backup atomico

O exporter:

1. valida a fonte regular e nao symlink;
2. abre a fonte SQLite com URI `mode=ro`;
3. cria um tempfile exclusivo no parent absoluto do target;
4. compara confinamento somente entre parents absolutos normalizados;
5. executa backup, commit, fechamento e `fsync` do tempfile;
6. promove com `os.replace` e retries limitados;
7. executa `fsync` do diretorio pai quando suportado;
8. limpa somente o tempfile pertencente a invocation atual.

Falhas anteriores ao `os.replace` preservam o target existente. Falhas reais de
durabilidade do diretorio nao sao transformadas em sucesso. O report publica
`parent_directory_fsync_status` como `ok`, `unsupported`, `not_attempted` ou
`blocked`.

O comando inline Docker preserva o mesmo contrato de normalizacao, confinamento,
source read-only, backup, `fsync`, promocao atomica, retries e cleanup proprio.

## Regressao

O teste dedicado cobre:

- output relativo equivalente ao runtime;
- source e target relativos;
- `working_directory` explicito;
- `mkstemp` absoluto corretamente confinado;
- rejeicao e cleanup de tempfile externo;
- preservacao do target em falha de backup ou promocao;
- concorrencia sem compartilhamento de tempfile;
- equivalencia canonica entre source e target;
- paridade do script inline Docker;
- falha real no `fsync` do parent.

Todos os testes usam paths temporarios. Nenhum teste inicia Docker ou escreve em
`data/runtime`.

## Seguranca operacional

As invariantes permanecem:

```text
paper_only=true
shadow_only=true
live_trading_enabled=false
live_release_allowed=false
canary_release_allowed=false
order_submission_enabled=false
real_order_submission_enabled=false
exchange_private_access=false
sends_orders=false
changes_risk=false
```

Nao foram alterados Compose, healthcheck, bootstrap, estrategia, risco, Qlib,
IA Shadow, notificacoes, Decision Ledger ou politica operacional.

## Certificacao pendente

V5 nao e reclassificado como aprovado por esta branch. O cold-start V5.2 somente
pode ocorrer depois de merge, baseline pos-merge e autorizacao operacional
separada.
