# Paper financial source authority preflight F05

F05 valida proveniencia, nao autoriza executar F04. Nao calcula PnL nem chama
`audit_paper_financial_population`. F03/F04 e seus contratos permanecem intactos.
Nesta implementacao, somente fixtures descartaveis foram acessadas.

## Fronteira de confianca

Uma declaracao F04 e seus hashes nao provam origem operacional. F05 exige uma
evidencia de origem emitida por observador independente e revisada fora do fluxo
do solicitante. O registro `INSTITUTIONAL_REVIEWS` associa os bytes exatos dessa
evidencia a emissor, produtor, revisor, referencia de revisao e validade.
Os quatro participantes (incluindo solicitante) devem ser distintos.

O registro institucional esta **vazio**: nenhuma fonte real foi aprovada nesta
branch. CLI e ambiente nao podem adicionar confianca. Os testes provisionam
registros exclusivamente em memoria, sem alterar a politica entregue.
Um hash registrado sem revisao independente nao satisfaz este procedimento.
O registro e uma raiz administrativa revisada, nao uma assinatura criptografica
nem atestacao automatica do host; seu provisionamento exige revisao institucional
separada da cadeia original, com prova de custodia e configuracao efetiva.

Sem registro, F05 bloqueia antes de ler snapshot, perfil ou autorizacao F04.
O exportador existente de snapshots nao emite sozinho essa prova independente;
F05 nao o executa, nao cria recibos e nao reconstrui evidencias ausentes.

## Cadeia de entrada

Todos os caminhos sao absolutos, explicitos, sem symlinks/junctions ou aliases.
O snapshot deve estar dentro de `--project-root`. Nao ha descoberta/fallback.

- `--origin-evidence`: schema `paper_financial_source_origin_f05_v1`, identidades
  `issuer_id/requester_id/producer_id/runtime_instance_id`, `runtime_image_sha256`,
  `observed_at_utc/expires_at_utc` UTC terminados em Z, `config_resolution_complete=true`,
  `scope` e hashes `effective_config_sha256/export_receipt_sha256/source_profile_sha256/
  f04_authorization_sha256`.
- `--effective-config`: JSON da configuracao efetiva observada, incluindo todos
  os overrides, `dry_run=true` e `db_url` SQLite absoluta. Nao basta config estatica
  sem prova da resolucao efetiva. Conteudo/URL nao sao copiados para stdout.
- `--export-receipt`: schema `paper_financial_source_export_receipt_f05_v1`, produtor
  e instancia, `source_db_url`, `captured_at_utc`, `export_method=SQLITE_BACKUP`,
  `source_query_only=true`, `consistent_export=true`, `scope`, `snapshot_path`,
  conjuntos completos `source_artifact_sha256_before/source_artifact_sha256_after/
  snapshot_artifact_sha256` (`db/wal/shm`, null so para sidecar ausente),
  `closed_trade_ids` unicos ordenados e contagens `population_total_trade_count/
  population_open_trade_count`. Os hashes da origem antes/depois devem concordar.
- `--source-profile/--source-authorization/--sqlite-path`: contratos existentes F04.

O `scope` contem exatamente `source_id/account_scope_hash/paper_scope/epoch_id/cohort_id`.
Uma unica populacao Control A ou Treatment B e um unico epoch/cohort devem concordar
em toda a cadeia. Colunas de escopo presentes no DB nao podem contradizer a cadeia;
na ausencia delas, o escopo depende da prova independente da populacao completa.
Membership fechado e contagens sao verificados na copia consistente do leitor
existente, com orders presentes e identidades nao ambiguas; nao se reconciliam
formulas financeiras aqui. Hashes DB/WAL/SHM e documentos sao revalidados ao final.

Validade F05 e limitada a uma hora para observacao e captura, sem timestamps futuros.
O relogio UTC real e consultado novamente ao final; nao ha override CLI do relogio.
Isso e freshness de custodia, nao inferencia de autoridade por datas dos trades.
Resultados sao deterministas para os mesmos inputs e janela de validade; expirar
a evidencia deve mudar o resultado para BLOCKED.

## Saida e execucao futura

CLI somente JSON stdout, exit 0 para `VALIDATED`, 2 para `BLOCKED`.
`VALIDATED` significa apenas `SOURCE_PREFLIGHT_VALIDATED_NOT_EXECUTION_AUTHORIZATION`.
Sempre: `execution_authorized=false`, `f04_executed=false`, `write_performed=false`,
`runtime_parity_certified=false`, `pnl_certified=false`, `economic_readiness=NOT_ASSESSED`.
O leitor reutilizado usa copia temporaria descartavel query-only fora das fontes;
nenhum snapshot original e criado, reparado, checkpointado ou modificado.

Execucao real continua bloqueada ate haver prova independente revisada, configuracao
efetiva do produtor, cadeia de custodia recente, escopo/membership exatos e autorizacao
operacional separada. F05 nao e um lock de runtime nem garante estabilidade futura:
F04 permanece inalterado e revalida seus proprios hashes durante a leitura.
Nenhuma certificacao de paridade, integridade financeira ou readiness decorre do F05.
