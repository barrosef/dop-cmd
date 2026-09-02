# ADR-0007 — `--dry-run` em todas as operações com efeito colateral

- **Status:** Aceito
- **Componentes:** transversal (`core/process.py`, `core/fs.py`, `git/*`, `platform/*`, `runtime/*`)

## Contexto

Operações do `dop` mutam repositórios Git, criam PRs reais e sobem containers.
Operadores e o agente de IA precisam **prever** o efeito de um comando antes de
executá-lo de verdade — especialmente em fluxos automatizados onde um comando
errado pode poluir o histórico remoto.

## Decisão

Adotar um *flag* global **`--dry-run`** (no parser raiz) propagado a todas as
camadas. O contrato é uniforme:

- `core/process.run_command(..., dry_run=True)` loga `WOULD RUN: <cmd>` e retorna
  sucesso (returncode 0) sem executar.
- `core/fs.write_text/write_json(..., dry_run=True)` é *no-op*.
- Cada provider de plataforma retorna um `PRResult` "esqueleto" (campos `None`)
  em dry-run, sem tocar a rede.
- Handlers passam `args.dry_run` adiante em **toda** chamada com efeito.

## Consequências

- ➕ Pré-visualização confiável de qualquer comando.
- ➕ Testabilidade: a maioria dos testes roda com `dry_run=True` e verifica os
  comandos que *seriam* executados, sem precisar de Git/rede reais.
- ➕ Em dry-run, `load_state` é chamado com `create=False` (não cria `.state.json`
  novo), evitando side-effects acidentais.
- ➖ Exige disciplina: toda nova função com efeito colateral precisa aceitar e
  honrar `dry_run`. É um invariante que revisões de código devem proteger.
