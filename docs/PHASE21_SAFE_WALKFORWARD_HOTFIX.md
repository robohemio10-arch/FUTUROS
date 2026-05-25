# Fase 21 Safe Walk-forward Hotfix

O walk-forward anterior foi considerado inconclusivo quando ficou vários dias sem retornar relatório final.

Este hotfix transforma a Fase 21 em uma avaliação offline controlada com:

- timeout;
- limite de linhas;
- folds limitados;
- fallback de modelo;
- relatório JSON;
- gráficos;
- status `ok`, `skipped` ou `error`;
- evidência separada.

A operação paper contínua deve rodar separadamente da avaliação walk-forward.
