# Qlib Long Economic Policy Challenger V2

## Objetivo

Congelar como candidato de pesquisa a arquitetura que apresentou edge econômico OOS no estudo de 08/09/2026 sem alterar runtime, risco ou execução.

## Mudanças em relação ao V1

- target de treino: `absolute_stressed_net_pnl`;
- universo de treino: todos os trades Paper causalmente disponíveis;
- elegibilidade de tratamento: somente `long`;
- shorts continuam no treino, mas não entram na população selecionada;
- quantis de calibração são calculados somente sobre trades elegíveis;
- grid de quantis elegíveis: `0.20/0.30/0.40/0.50/0.60/0.70/0.80/0.85`, para não reduzir artificialmente a amostra quando a política estrutural já limita o universo;
- baseline econômico de calibração continua sendo todos os trades;
- threshold continua escolhido exclusivamente em dados passados;
- avaliação final continua no gate canônico de 3 folds com stress de execução.

## Motivação econômica

O V1 com target normalizado por notional não encontrou threshold lucrativo nos três folds reais. O estudo controlado com 905 closed Paper outcomes e market context PIT rematerializado mostrou que o target absoluto de Net PnL stressed, combinado com elegibilidade long-only, produziu população OOS positiva e estável no gate histórico. Como esta arquitetura foi definida após inspeção do histórico, ela permanece estritamente research-only e exige confirmação prospectiva em novos trades antes de qualquer discussão de promoção.

## Segurança

- `paper_only=true`
- `shadow_only=true`
- `research_only=true`
- `operational_authority=false`
- `promotion_allowed=false`
- `changes_risk=false`
- `sends_orders=false`
- `exchange_private_access=false`
- `writes_runtime=false`

## Gate futuro

Apenas nova evidência prospectiva pode elevar a confiança. O candidato não deve ser promovido com base no mesmo conjunto histórico usado para sua seleção arquitetural.
