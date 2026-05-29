# No Duplicate Execution Policy

Esta política define as regras anti-duplicação para o runtime FUTUROS/SmartCrypto. O objetivo é evitar duplicação de serviços, sinais, importações, feedback, decisões e risco de ordens duplicadas no ambiente paper/shadow.

## Regra Central

O projeto permanece paper/shadow only, com live trading bloqueado. Nenhum processo duplicado pode assumir autoridade operacional paralela.

## Proibições

- Proibido rodar dois `START_PAPER_24H` simultâneos.
- Proibido rodar dois loops de renovação de sinal operacional simultâneos.
- Proibido IA Shadow escrever `active_freqtrade_signals.json`.
- Proibido importar trades fora da Fase 5.
- Proibido ler SQLite do Freqtrade fora da Fase 14, exceto diagnóstico explícito e read-only.
- Proibido editar `trades_master` manualmente.
- Proibido commitar `data/`, logs, SQLite, Parquet, CSV, XLSX, JSON runtime, JSONL runtime ou zip evidence.
- Proibido alterar Docker, `.env`, `START_PAPER_24H`, strategy ou lógica de execução para contornar guardrails.

## Checagem Antes De Iniciar Paper

Antes de iniciar qualquer sessão paper, rode:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "START_PAPER_24H|MONITOR_PAPER_SESSION" } |
  Select-Object ProcessId, CommandLine
```

Se já existir `START_PAPER_24H`, não iniciar outro.

Se já existir `MONITOR_PAPER_SESSION` ligado à sessão vigente, não iniciar outro monitor concorrente.

## Autoridade Única Por Função

- Sinal operacional: somente Phase13.
- Fonte pinned: somente `data/runtime/active_freqtrade_signals.json`.
- Executor paper/dry-run: somente Freqtrade.
- Feedback paper: somente Fase 14 por snapshot SQLite.
- Importação/rebuild de trades e datasets: somente Fase 5.
- Observabilidade: Dashboard read-only.
- Observador/filtro: IA Shadow sem escrita operacional.

## O Que Fazer Em Caso De Duplicação

1. Não iniciar novo processo.
2. Registrar o processo duplicado detectado.
3. Encerrar apenas o processo duplicado confirmado, sem derrubar container paper válido.
4. Revalidar que há somente uma autoridade operacional por função.
5. Não editar arquivos runtime manualmente para "corrigir" o estado.

## Evidência E Git

Relatórios, SQLite, Parquet, CSV, XLSX, JSON runtime, JSONL runtime, logs e zip evidence são runtime state. Eles não devem ser versionados.

Documentação e testes de contrato podem ser versionados quando não contêm dado sensível nem output runtime.
