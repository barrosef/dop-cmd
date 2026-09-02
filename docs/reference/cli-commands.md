# Referência — Comandos da CLI `dop`

> **Fonte de verdade.** Reconstruído de `src/dop/cli.py` (`build_parser`) na v0.5.0.
> Substitui a seção "CLI Commands" do `README.md` da raiz, que está desatualizada.

Flags globais (válidas para todos os comandos):

| Flag | Efeito |
|---|---|
| `--dry-run` | Loga `WOULD RUN: …` sem executar git/az/compose/IO. |
| `--workspace <nome>` | Seleciona o workspace (default: auto-detect pelo CWD). |

Comandos com `<JIRA>` validam a chave contra `jira_key_pattern` e resolvem aliases
para a chave mestre antes de operar.

## Demanda

### `dop demand-init <JIRA> --repos <csv> [--linked <csv>] [--branch <nome>]`
Inicializa a demanda: marca repos impactados/`skipped`, registra Jiras linkadas
(cria aliases), faz pull das branches longas dos repos impactados, cria a feature
branch (default = `<JIRA>`) a partir da `base_branch` e faz push inicial.

### `dop link <mestre> <chave…>`
Agrupa uma ou mais Jiras sob a chave mestre (cria aliases + atualiza
`linkedJiraKeys`).

### `dop update-repos [--repo <r>]`
Pull de todas as branches longas conhecidas (silencioso se a branch não existe).

### `dop git-push <JIRA> [--repo <r>] [--branch <b>] [--force]`
Push da feature branch da demanda. `--force` usa `--force-with-lease` (pós-rebase).

### `dop pr-publish <JIRA> --repo <r> --title <t> [--body <d>] [--source-branch <b>] [--target <b>] [--commit-message <m>] [--force] [--force-new-pr]`
Commit pendente (se houver) + push + cria PR para **todos** os `pr_targets` do repo
(ou só `--target`). Reaproveita PR ativo existente para a mesma `source→target`
(salvo `--force-new-pr`). Volta o repo para a `base_branch` ao final.

### `dop teams-message <JIRA>`
Gera `03-pr-team-message.md` a partir de `state.prs` e imprime no stdout.

### `dop solve-conflict <JIRA> [--repo <r>]`
`fetch` + checkout da feature + `rebase` sobre a `base_branch`. Em conflito, lista
arquivos e para no primeiro repo; instrui resolução manual + `dop git-push … --force`.

### `dop show <JIRA>`
Imprime o `.state.json` da demanda (resolvendo alias).

## Integração (sem Jira; não alteram `.state.json`)

### `dop integrate-desenv [--repo <r>]`
Para cada repo com commits em `origin/desenv..origin/<base>`, cria PR `base → desenv`
(pula se não há commits ou já existe PR ativo). Reporta conflitos e a próxima ação.

### `dop prepare-merge-conflicts [--repo <r>]`
Cria/recria a branch `merge-conflicts-desenv-from-OG-GLOBAL` a partir de
`origin/desenv` e tenta `merge origin/<base>`. Em conflito, lista arquivos.

### `dop finish-merge-conflicts [--repo <r>]`
Valida a branch auxiliar (sem merge pendente, com commits), faz push e cria PR →
`desenv`.

## Runtime (`docker compose`)

### `dop start <apps…> [--no-deps]`
Sobe apps (nome ou alias). Auto-inclui o BE de um FE (salvo `--no-deps`), checa
portas (`lsof`), builda FEs, infere URLs de BE (`docker/.env.runtime`) e sobe
`mongodb`+`allure`+apps com `--wait`.

### `dop stop [apps…]`
Para apps (vazio = todos).

### `dop restart <apps…> [--no-deps]`
`stop` + `start` dos apps.

### `dop rebuild <fe-apps…>`
Rebuild de FE (build no host) + restart do serviço nginx. Apps: `osf`, `pfe`, `cef`.

### `dop status` / `dop ps`
Status dos containers (apps + infra) com marcadores ✔/✗ (estado, health, porta).

### `dop log <apps…> [-f|--follow] [-n|--tail N] [--since <dur>]`
Segue logs (via `os.execvp`, modo interativo).

### `dop clean [--m2] [--node-modules] [--allure] [--all]`
Remove volumes/caches Docker (Maven, node_modules, Allure, ou tudo).

## E2E / Relatórios

### `dop e2e <alvo…> [--headed] [-k <filtro>] [--max-strikes N] [--reset-strikes]`
Roda Playwright por suíte, chave Jira (auto-descobre suítes) ou arquivo. `--headed`
= modo visual (X11). *Strikes* limitam retentativas (1–10).

### `dop codegen <suite> [--url <u>] [--out <arquivo>]`
Playwright codegen (grava interações). Default URL `http://localhost:5173`; default
out `tests/recordings/recording_<data>.py`.

### `dop report serve | open [suite] [jira] | clean [--keep N]`
`serve` sobe o Allure; `open` abre no navegador (opcionalmente o relatório de uma
suíte/Jira); `clean` mantém as últimas N execuções por suíte (default 5).

---

## ⚠️ Comandos do README da raiz que NÃO existem (removidos)

Os seguintes comandos aparecem no `README.md` da raiz mas foram **removidos** na v0.5
(ver [ADR-0009](../adr/0009-remover-stage-gates.md)):
`context-approved`, `plan-approved`, `build-passed`, `change-approved`,
`conflict-solved`, `rerun`, `reset`, e o *entry point* `dop-devops`.
