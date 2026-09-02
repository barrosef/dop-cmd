# Arquitetura — Visão Geral

> Reconstruído de `src/dop/` na versão 0.5.0.

## 1. Propósito

O `dop` é uma CLI Python (`requires-python >= 3.11`) instalada via `pyproject.toml`
com um único *entry point*:

```toml
[project.scripts]
dop = "dop.cli:main"
```

Ela encapsula operações de Git, criação de Pull Requests multi-plataforma,
orquestração de `docker compose` e execução de testes E2E, tudo guiado por um
arquivo de **estado por demanda** (`.state.json`) e por um arquivo de
**configuração por workspace** (`~/.config/dop/config.toml`).

## 2. Mapa de módulos

```
src/dop/
├── cli.py                  # Entry point: parser argparse + handlers de demanda/integração
├── git_askpass.py          # Helper invocado pelo git (GIT_ASKPASS) p/ HTTPS sem vazar token
│
├── config/                 # Camada de configuração (TOML → dataclasses)
│   ├── schema.py           #   Dataclasses: WorkspaceConfig, RepoConfig, RuntimeConfig, AppConfig...
│   ├── loader.py           #   Carrega ~/.config/dop/config.toml (tomllib)
│   └── discovery.py        #   Auto-detecção de workspace pelo CWD
│
├── core/                   # Utilitários transversais (sem dependência de domínio)
│   ├── errors.py           #   Hierarquia de exceções (MCPError e filhas)
│   ├── state.py            #   .state.json: load/save, repos, prs, e2e, runtime, aliases
│   ├── security.py         #   redact()/guard_text(): redação de segredos + anti env-dump
│   ├── process.py          #   run_command(): subprocess com dry-run e redação
│   ├── fs.py               #   IO de arquivos atômico + dry-run
│   ├── hashing.py          #   SHA256 de bytes/arquivos
│   └── logging_utils.py    #   Logger com redação, logs por demanda
│
├── git/                    # Operações Git
│   ├── operations.py       #   ~25 funções: pull, push, rebase, merge, checkout, conflitos...
│   ├── branch_parser.py    #   Parser da tabela "Branch de Trabalho" em markdown
│   └── auth/               #   Abstração de autenticação
│       ├── base.py         #     GitAuthProvider (ABC)
│       ├── token.py        #     TokenAuth (HTTPS + GIT_ASKPASS)
│       ├── ssh_rsa.py      #     SshRsaAuth
│       ├── ssh_ed25519.py  #     SshEd25519Auth
│       └── __init__.py     #     build_auth_provider() (factory)
│
├── platform/               # Abstração de plataformas de PR
│   ├── base.py             #   PRResult (dataclass) + PlatformProvider (ABC)
│   ├── azure.py            #   Azure DevOps via `az repos pr` CLI
│   ├── github.py           #   GitHub via PyGithub
│   ├── gitlab.py           #   GitLab via python-gitlab (Merge Requests)
│   └── __init__.py         #   build_platform_provider() (factory)
│
└── runtime/                # Runtime local + E2E (adicionado na v0.5)
    ├── compose.py          #   Construtores de comandos `docker compose`
    ├── resolve.py          #   Resolução de aliases de apps + inferência de URLs
    ├── e2e.py              #   Resolução de alvos E2E (suite/JIRA/arquivo) + run numbering
    └── handlers.py         #   Handlers: start, stop, restart, rebuild, status, log,
                            #            e2e, codegen, report, clean
```

## 3. Camadas e dependências

```
┌──────────────────────────────────────────────────────────────┐
│  cli.py  (argparse → dispatch para handlers)                   │
└───────────────┬───────────────────────────┬──────────────────┘
                │                            │
        Handlers de demanda          Handlers de runtime
        (em cli.py)                  (runtime/handlers.py)
                │                            │
   ┌────────────┼─────────────┐             │
   ▼            ▼             ▼              ▼
 git/        platform/     core/state    runtime/compose
 (operations) (PR)         (.state.json)  runtime/resolve
   │            │             │            runtime/e2e
   ▼            ▼             ▼              │
 git/auth    PyGithub/      core/fs         ▼
 (auth)      gitlab/az      (IO atômico)  docker compose (subprocess)
   │            │
   ▼            ▼
 git_askpass  core/process (run_command + redação)
   │            │
   └────────────┴──► core/security (redact/guard_text)
                     core/logging_utils (Logger)
```

Regra de ouro: **`core/` não conhece domínio**. `git/`, `platform/` e `runtime/`
dependem de `core/` e de `config/`, mas não umas das outras (exceto `platform/azure.py`,
que reusa `git.operations.get_remote_url`/`repo_path` para derivar org/project do remote).

## 4. Princípios arquiteturais transversais

Estes princípios são impostos de forma consistente em todo o código e são
formalizados como ADRs:

1. **Estado como fonte de verdade** — toda operação de demanda carrega, modifica e
   salva `.state.json`. ([ADR-0003](../adr/0003-estado-como-fonte-de-verdade.md))
2. **`--dry-run` em tudo** — qualquer side-effect (git, az, compose, IO) respeita
   `dry_run` e loga `WOULD RUN: …`. ([ADR-0007](../adr/0007-dry-run-everywhere.md))
3. **Redação de segredos** — toda saída de subprocess, log e erro passa por `redact()`;
   `guard_text()` bloqueia dumps de `os.environ`. ([ADR-0006](../adr/0006-redacao-de-segredos.md))
4. **Multi-plataforma via ABC** — operações de PR passam pela ABC `PlatformProvider`.
   ([ADR-0004](../adr/0004-abstracao-multiplataforma-pr.md))
5. **Multi-workspace via TOML + auto-detect** — um único config descreve N workspaces,
   selecionados por `--workspace` ou pelo CWD. ([ADR-0002](../adr/0002-configuracao-multiworkspace-toml.md))
6. **Logs/registros append-only** — `commands_log` e `prs` são apenas-acréscimo e
   verificados contra truncamento ao salvar.

## 5. Fluxo de execução de um comando

```
main(argv)
  ├─ build_parser().parse_args()          # argparse (SecureArgumentParser redige erros)
  ├─ get_workspace(args.workspace)         # explícito ou auto-detect por CWD
  ├─ validate_jira_key() + resolve_alias() # se o comando tem jira_key
  ├─ get_logger(jira_key, command)         # logger com redação, log por demanda
  ├─ build_auth_provider(workspace)        # token | ssh_rsa | ssh_ed25519
  └─ args.func(args, logger, workspace, auth)
        └─ handler executa (lê/grava .state.json, chama git/platform/runtime)
  (exceções → redact() + guard_text() → "ERROR: …" + exit code 1)
```

## 6. Persistência

| Artefato | Caminho | Descrição |
|---|---|---|
| Configuração | `~/.config/dop/config.toml` (ou `$DOP_CONFIG`) | N workspaces, repos, runtime, apps. |
| Estado da demanda | `<root>/<demands_dir>/<JIRA>/.state.json` | Repos impactados, PRs, E2E, runtime, log de comandos. |
| Alias de demanda | `<root>/<demands_dir>/<JIRA>/.alias` | Aponta uma Jira agrupada para a chave mestre. |
| Logs | `<root>/<demands_dir>/<JIRA>/logs/<ts>-<cmd>.log` | Log por execução, com redação. |
| Mensagem Teams | `<root>/<demands_dir>/<JIRA>/03-pr-team-message.md` | Resumo de PRs gerado. |
| Env de runtime | `docker/.env.runtime` | URLs de back-end computadas, injetadas no compose. |
| Relatórios E2E | `e2e/reports/<JIRA>/<suite>/run-<N>/` | Resultados Allure por execução. |

## 7. Evolução histórica (do Git)

| Fase | Marco | Commits-chave |
|---|---|---|
| 1. Scripts MCP | `scripts/mcp/cli.py` dentro do projeto optum (gates + IA-First). | (pré-repo, ver `docs/prompts/`) |
| 2. `iadev` | CLI standalone com gates + `devops` (git/PR). | `74e665a`, `029c588` |
| 3. `dop` | Renomeado `iadev` → `dop`; unificação `dop`+`dop-devops`; multi-org. | `63126ec`, `cb63422` |
| 4. Runtime v0.5 | Config/state estendidos; módulo `runtime/` (compose, resolve, e2e). | `f70743c` … `6ef9cc4` |
| 5. Pós-gates | Remoção dos *stage gates*; Allure 3; nginx p/ FEs; rename de apps. | `afe50f8`, `e04183d` |

Ver os ADRs para o racional de cada decisão.
