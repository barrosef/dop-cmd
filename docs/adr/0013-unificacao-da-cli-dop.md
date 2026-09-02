# ADR-0013 — Renomeação `iadev`→`dop` e unificação de `dop-devops`

- **Status:** Aceito
- **Commits:** `63126ec` ("rename project iadev -> dop"), `cb63422` ("unify dop-devops into dop CLI, add multi-org support"), `afe50f8` ("rename optumsupport-* → optum-support-*")

## Contexto

A ferramenta nasceu como `iadev` ("IA dev") e tinha **dois** *entry points*:
`iadev` (comandos de gate) e `iadev`/`devops` (operações git/PR, em `devops.py`).
Dois binários para um mesmo fluxo confundiam o uso e duplicavam *boilerplate*
(parsing, contexto, dispatch). Além disso, repos em múltiplas organizações Azure
não eram suportados.

## Decisão

1. **Renomear** o projeto e o pacote para **`dop`** (*DevOps Pipeline*), com um único
   *entry point* `dop = dop.cli:main`.
2. **Fundir** `dop-devops` na CLI única `dop`: todos os comandos (demanda,
   integração, runtime, E2E) vivem sob `dop <subcomando>`.
3. **Multi-org**: permitir override de organização/projeto Azure por repo
   (`RepoConfig.azure_org`/`azure_project`), com cadeia de prioridade
   config-por-repo → env vars → URL do remote (ver [ADR-0004](0004-abstracao-multiplataforma-pr.md)).
4. **Padronizar nomes** de apps/serviços (ex.: `optumsupport-*` → `optum-support-*`)
   para casar com os nomes de serviço do `docker-compose.yml`.

## Consequências

- ➕ Uma só ferramenta, uma só mentalidade: `dop`.
- ➕ Suporte a repositórios espalhados por várias orgs Azure no mesmo workspace.
- ➕ Nomes consistentes entre config, estado e compose reduzem erros de resolução.
- ➖ Quebra de compatibilidade com invocações antigas `iadev …`/`dop-devops …`
  (documentação e automações externas precisaram ser atualizadas).
- ➖ Resquícios do nome antigo permanecem nos prompts históricos
  (`docs/prompts/prompt-iadev-*`), preservados como artefato.
