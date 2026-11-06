# Contrato V2 de fontes offline Paper B

A implementação L3.14 reconhece explicitamente a proposta L3.13 em um descritor
read-only. Ela mantém a sucessora sem identidade, ordinal, raiz autoritativa ou T0
alocados. O estado da proposta continua PLANNED_UNREGISTERED.

**Contrato aprovado e confiança**

O schema do descritor é paper_b_storage_cohort_descriptor_v2. Seu conteúdo inclui
a proposta aprovada, fingerprints, bindings explícitos de fontes e o selo
descriptor_sha256. Campos desconhecidos, bindings duplicados e permissões
divergentes bloqueiam a validação.

As cópias empacotadas de approved_l313_proposal.json e
approved_l313_proposal.schema.json preservam a especificação aprovada. O módulo
confere o SHA físico da proposta e seu selo canônico
bf85cc70d46cdcca03df193c214b16884461c02d274d09f294073f2c4975254e.
A igualdade com essa proposta impede preencher T0, trocar V3 ou ampliar os mounts
através de um descritor resselado.

Os dois JSONs aprovados têm uma exceção exata `-text` em `.gitattributes`.
Isso preserva seus bytes CRLF e hashes físicos em checkouts Windows e Linux;
os demais arquivos seguem a normalização já existente do repositório.
Nesses dois arquivos, o verificador de whitespace reconhece CR como parte de
CRLF e preserva as verificações de espaços finais, EOF e espaço antes de tab.

A referência histórica é o registro create-once da Epoch 2. A validação reutiliza
causal_epoch2_foundation.validate_registration, exige os selos aprovados do
registro, manifesto e baseline e fixa o SHA físico histórico
beb3c929837869e901b3a623ab5784988ddbdc06ebde2b2174e726e8e0231306.
O registro V1 e seus validadores continuam preservados.

O chamador fornece fingerprints conhecidos e observações de fonte provenientes
de seu contexto de pesquisa autorizado. A implementação compara esse contexto
externo com os bindings declarados. Ela verifica arquivos locais e hashes, mas a
coleta e a autenticação dos fatos Docker, ACL, UID e proveniência dessas
observações continuam sendo responsabilidade do produtor autorizado da evidência.
Um descritor auto-selado, isoladamente, não atesta um deployment ativo.

**APIs**

| API | Comportamento |
| --- | --- |
| storage_cohort_contract_v2.validate_descriptor | Valida mapping, registro predecessor, SHA físico informado e fingerprints conhecidos. Retorna ValidatedDescriptor com snapshots canônicos imutáveis. |
| storage_cohort_contract_v2.load_descriptor | Lê descritor e predecessor em paths explícitos, calcula o SHA físico do predecessor e chama a validação. |
| evidence_sources_v2.resolve_source | Recebe descritor validado, componente, research_read, referência lógica, fingerprints e observações explícitas. Retorna SourceResolution. |
| offline_evidence_readers_v2.build_offline_evidence_reader_v2 | Usa os bytes verificados e os avaliadores puros existentes para o leitor selecionado. |
| economic_phase_final_forward_proof.build_economic_phase_final_forward_proof_v2 | Reconhece o descritor e retorna o bloqueio de pré-ativação, sem coletar runtime ou calcular população. |

O resolver revalida o contexto imutável a cada chamada. Exige uma única
observação igual ao binding aprovado, path local absoluto sob raiz autorizada,
mount e acesso RO exatos e SHA do artefato correspondente. A leitura estável
limita tamanho e rejeita alteração durante leitura, symlinks e junctions.
O limite local é 32 MiB. O leitor global de ativação continua com 16 MiB.
Paths de runtime e bancos operacionais são recusados.

Um resultado válido contém path interno, identidade, namespace, mount esperado,
acesso, hash e bytes. Um bloqueio contém motivo estruturado e raw=None.
Fonte ausente não produz coleção vazia nem PnL zero.

Fontes congeladas V3 e WQ6 têm paths e identidades próprios. JSON histórico exige
epoch_id explícito igual ao binding; experiment_id, quando presente, também é
conferido. A falta desse vínculo bloqueia V2. Recuperá-lo exige evidência
autoritativa, sem editar retrospectivamente os relatórios oficiais.

A revisão L3.15 reforçou a admissão semântica dos relatórios materializados:
B14 confere a identidade V3 completa de B09 e recalcula o selo B13 com a função
oficial do produtor. B15/B16 recalculam o selo do scorecard B14 antes de avaliar
seus inputs. Nos selos B13/B14, todos os campos, inclusive `source` se presente,
participam do hash, exceto o próprio selo. Nenhum selo novo foi inventado para
os schemas B09–B12. JSON histórico cujo aninhamento excede a capacidade do parser
retorna bloqueio estruturado, sem bytes ou avaliação parcial.

**Entradas de linha de comando**

Os comandos abaixo exibem a interface completa sem iniciar coleta.

    python -B scripts/build_paper_b_offline_evidence_v2.py --help
    python -B scripts/build_economic_phase_final_forward_proof_v1.py --help

O primeiro exige --descriptor, --predecessor, --fingerprints,
--observations e --reader. O documento de observações contém somente a chave
observations, com uma lista de bindings observados. Leitores aceitos são B09,
B10, B14, B15, B16, economic_monitor e parity_collector.

O comando B17 seleciona V2 com os três argumentos --v2-descriptor,
--v2-predecessor-registration e --v2-fingerprints. Um contexto V2 incompleto
é rejeitado. Os modos históricos continuam disponíveis pelos argumentos legados
e pelas APIs existentes. A seleção V2 não volta silenciosamente ao coletor V1.

As CLIs emitem JSON no stdout. Um relatório blocked encerra com código 2. A CLI
do leitor offline retorna 0 para um diagnóstico ok ou waiting.

**Matriz dos onze componentes**

| Componente | Situação offline V2 | Limite |
| --- | --- | --- |
| Publisher F02 | Fora do escopo de alteração | O resolver recusa o papel operacional. Mudança de identidade e wiring exige etapa autorizada. |
| Consumidor F02 | Fora do escopo de alteração | Estratégia, callbacks e autoridade síncrona qualificados permanecem preservados. |
| Monitor econômico | Bloqueado | A geração precisa de coorte registrada e inputs financeiros autorizados. O adaptador não lê SQLite nem grava relatório. |
| Coletor de paridade | Bloqueado | Falta atestação versionada do deployment real. A resolução de relatório é diagnóstica. |
| B09 | Integrado | Avalia o snapshot V3 exato, conferindo schema, coleções, identidade e selo. |
| B10 | Compatível com relatório materializado | Confere os quatro inputs congelados e sua vinculação ao relatório existente. Não executa treino ou nova ablation. |
| B14 | Integrado como avaliação pura | Exige todos os relatórios explícitos e seus vínculos históricos válidos. |
| B15 | Integrado como avaliação pura | Exige scorecard e evidência de sleeves admissíveis. |
| B16 | Integrado como avaliação pura | Exige scorecard e evidência Council A/B admissíveis. |
| B17 | Integrado no gate de pré-ativação | Sem registro sucessor e T0, continua blocked sem tentar certificação. |
| Fundação Epoch 2 | Compatível com validação histórica | Registro V1 intacto. Este código não cria registro sucessor. |

A integração com arquivos locais reais confirmou B09 ok. Os relatórios históricos
disponíveis para B10, B14, B15 e B16 foram bloqueados por ausência de vínculo
explícito de época. Seus hashes foram preservados. Essa constatação não altera
os resultados nem os contratos dos leitores históricos V1.

B10 usa somente um relatório já materializado, associado por hashes aos quatro
inputs WQ6. Seu escopo é verificar a identidade e os metadados desses artefatos.
Esse caminho não faz nova validação econômica do produtor, não reproduz
treinamento nem equivale à reconstrução do dataset e dos splits. Um relatório
B10 blocked continua blocked.

B17 mantém os gates de 45 dias, 200 decisões resolvidas, 50 trades Treatment
fechados, cobertura de 99%, zero lacunas inexplicadas, Profit Factor de 1,10,
stress adicional de 5 bps, PnL líquido e expectancy positivos, delta pareado
positivo e limite inferior do IC95% positivo. O bootstrap preserva 5.000
reamostragens e seed 42.
No modo V2 não registrado, valores desconhecidos são None, com
certification_attempted=false e evidence_status=NOT_COLLECTED.

**Validação e limites**

Os testes dedicados cobrem integridade do descritor, identidade histórica,
ambiguidades, acessos, hashes, travessia, aliases, ausência de evidência,
semântica da época e bloqueio B17 antes de qualquer coleta operacional.
Cabeçalhos históricos sintéticos substituem explicitamente o validador V1 nos
testes unitários herméticos. A integração com o arquivo V1 real executa o
validador oficial sem esse substituto.
As regressões de admissão semântica usam o resolver real; os probes independentes
da L3.15 também usam o registro V1 real e preservam evidências antes/depois.

Executar no checkout isolado com as dependências de teste já disponíveis.

    python -B -m pytest -q tests/test_storage_cohort_contract_v2.py tests/test_offline_evidence_readers_v2.py tests/test_economic_phase_final_forward_proof_v2.py

Resultados finais de testes e regressões acompanham o relatório externo da
entrega. A contagem pode variar por capacidade do sistema operacional, devendo
ser conferida com motivos de skip e exit code.

O descritor não concede autoridade operacional. Deploy, migração, mudança de
mounts, registro de época, proteção de posições e autorização de uma nova janela
permanecem sujeitos ao plano e às aprovações separados da L3.13. A janela
residual callback → submissão da ordem da F02 continua sem atomicidade
comprovada. O runtime ativo permanece FAIL_CLOSED_NOT_PROVEN.
