# ADR-0009 — Remoção dos *stage gates* em favor de fluxo conversacional

- **Status:** Aceito
- **Substitui:** o modelo de *stages* monotônicos do antigo `iadev`/MCP
- **Commits:** `e04183d` ("drop legacy stage gates"), e a docstring de `cli.py`
  ("v0.4 — fluxo conversacional, sem stages")
- **Evidências de remoção (git status):** exclusão de `src/dop/devops.py`,
  `tests/test_state_transitions.py`, `tests/test_pr_ops.py`,
  `tests/test_solve_conflict.py`, `tests/test_conflict_solved_rebase.py`.

## Contexto

A primeira geração da ferramenta (scripts MCP → `iadev`) modelava a demanda como
uma **máquina de estados com gates monotônicos**: `context_approved` →
`plan_generated`/`rfc_generated` → `build_passed` → `change_approved` →
`prs_created` → `conflict-resolution` → `done`. Funções como `advance_stage`,
`require_stage` e `reset_stage` impunham a ordem; comandos como `context-approved`,
`plan-approved`, `build-passed`, `change-approved`, `conflict-solved` eram os gates.

Na prática, o fluxo conduzido por IA é **conversacional e não-linear**: o agente
decide a próxima ação pelo contexto, não por um autômato rígido. Os gates geravam
atrito (precisava "aprovar" estágios artificiais), acoplavam comandos a transições
e aumentavam a superfície de bugs (ver o bug de filtro de status documentado em
`docs/prompts/prompt-iadev-v1.2.md`).

## Decisão

Remover completamente a máquina de *stages*:

- Eliminar `advance_stage`/`require_stage`/`reset_stage` e o campo `stage` do estado.
- Eliminar os comandos de gate (`context-approved`, `plan-approved`, `build-passed`,
  `change-approved`, `rerun`, `reset`) e o *entry point* `dop-devops`.
- Manter o `.state.json` como **registro de fatos** (repos, PRs, E2E, log de comandos),
  não como **autômato**. Idempotência e segurança passam a vir de:
  - re-consulta ao git/plataforma (ex.: `list_prs`, detecção de conflito);
  - logs append-only;
  - operações seguras por padrão (`--force-with-lease`, reuso de PR).

## Consequências

- ➕ Comandos viram operações ortogonais e componíveis; o agente orquestra a ordem.
- ➕ Menos estado a manter consistente → menos bugs de transição.
- ➕ Re-execução natural: um comando faz a coisa certa independentemente do "estágio".
- ➖ O `README.md` da raiz ficou **desatualizado** (ainda lista comandos de gate). A
  referência canônica passa a ser [`reference/cli-commands.md`](../reference/cli-commands.md).
- ➖ Perde-se a imposição automática de ordem; a disciplina de processo migra para o
  agente/operador e para os PRDs.
