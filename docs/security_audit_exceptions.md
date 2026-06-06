# Security audit exceptions

Esta branch não possui exceções `pip-audit` ativas. O lock direto foi atualizado
para remover vulnerabilidades diretas conhecidas em `pyarrow`, `pytest` e
`streamlit`.

## Bandit

O alvo `make security` executa `bandit` no escopo institucional inicial:

```bash
python -m bandit -q -r smartcrypto/runtime scripts/generate_project_manifest.py scripts/scan_versioned_secrets.py --skip B608,B310 --severity-level medium --confidence-level medium
```

Exceções rastreadas:

- `B608`: SQL dinâmico genérico. O escopo atual do Bandit não cobre módulos que
  constroem SQL com nomes de tabela controlados por contrato interno. A dívida
  ampla segue fora do escopo desta branch para evitar refatoração funcional.
- `B310`: `urlopen` genérico. Rotinas de dados públicos usam endpoints públicos
  sem credenciais; o escopo atual do Bandit evita false positives em coletores
  históricos e runtime de mercado.

Essas exceções são explícitas, não silenciosas. Qualquer nova expansão de
escopo do Bandit deve remover ou justificar exceções adicionais.
