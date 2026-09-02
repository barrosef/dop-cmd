# ADR-0008 — Fluxo Git operacional (feature branches, integração diária, conflitos)

- **Status:** Aceito
- **Componentes:** `cli.py` (handlers de demanda/integração), `git/operations.py`
- **Origem:** corresponde ao histórico "ADR-06 — fluxo Git operacional" citado em
  `docs/prompts/prompt-iadev-v2.md` (que vivia no repositório `optum`); aqui
  documenta-se a **implementação** atual no `dop`.

## Contexto

O projeto-alvo (Optum) usa um modelo de branches longas: cada repo tem uma branch
base (ex.: `OG-GLOBAL`) e uma branch de integração (`desenv`). Demandas são
desenvolvidas em *feature branches* nomeadas pela chave Jira e integradas via PR.
Conflitos surgem em dois pontos: feature→base e base→desenv.

## Decisão

Implementar o fluxo em comandos discretos, divididos por dependência de Jira:

### Comandos de demanda (dependem de `JIRA-KEY` + `.state.json`)

- **`demand-init`** — registra repos impactados, atualiza branches longas
  (`pull_branch_if_exists`), cria/checa a feature branch e faz push inicial.
- **`git-push`** — push da feature branch (com `--force-with-lease` se `--force`).
- **`pr-publish`** — commit pendente + push + cria PR para todos os `pr_targets`
  do repo (reusando PR ativo existente, salvo `--force-new-pr`); volta o repo para a
  base branch ao final.
- **`solve-conflict`** — `git fetch` + checkout da feature + `rebase` sobre a base;
  em conflito, lista arquivos e para no primeiro repo conflitante, instruindo
  resolução manual + `git-push --force`.
- **`teams-message`** — gera `03-pr-team-message.md` resumindo os PRs.

### Comandos de integração (independentes de Jira)

- **`integrate-desenv`** — para cada repo com commits em `base..desenv` ausentes,
  cria PR `base → desenv` (pula se não há commits ou se já existe PR ativo).
- **`prepare-merge-conflicts`** — cria/recria branch fixa
  `merge-conflicts-desenv-from-OG-GLOBAL` a partir de `origin/desenv` e tenta
  `merge origin/<base>`; em conflito, lista arquivos para resolução manual.
- **`finish-merge-conflicts`** — valida branch/merge, push da branch de conflitos e
  cria PR → `desenv`.

### Invariantes de segurança do fluxo

1. Nunca push direto em branches longas (`OG-GLOBAL`/`desenv`/`master`).
2. Sempre `--force-with-lease`, nunca `--force` cru (ver `force_push_branch`).
3. Nunca merge automático de PR — a aprovação é humana na plataforma.
4. Operações de merge/rebase retornam `(success, conflicts: list[str])`; conflito
   não é exceção, é fluxo de controle. Estados pendentes são detectados por marcadores
   no `.git/` (`MERGE_HEAD`, `rebase-merge/`, `rebase-apply/`).

## Consequências

- ➕ O fluxo mapeia 1:1 a prática de integração do time; o agente conduz, o humano
  resolve conflitos.
- ➕ Comandos de integração não tocam `.state.json` (são manutenção de repo).
- ➕ Reuso de PR e detecção de conflito tornam os comandos seguros para re-execução.
- ➖ Os nomes de branch de integração (`desenv`, `merge-conflicts-desenv-from-OG-GLOBAL`)
  estão **constantes em `cli.py`** (`DESENV_BRANCH`, `MERGE_CONFLICTS_BRANCH`), não
  no config — específicos do workspace Optum. Generalizá-los é débito técnico conhecido.
