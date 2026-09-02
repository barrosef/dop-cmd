# Prompt: iadev v2 — Integração Diária e Resolução de Conflitos

## Contexto

Você é o agente Claude responsável por implementar novas funcionalidades no CLI `iadev`, localizado em `/opt/wks/dbo/iadev`.

O `iadev` é uma ferramenta CLI Python que orquestra o fluxo de desenvolvimento IA-First do projeto Optum, gerenciando gates humanos, estado de demandas, operações Git e criação de PRs no Azure DevOps.

Este prompt descreve as funcionalidades que devem ser adicionadas ao iadev para cobrir o fluxo Git operacional definido no **ADR-06** (`/opt/wks/csptech/optum/docs/ADR/ADR-06-fluxo-git-operacional.md`). **Leia o ADR-06 completo antes de iniciar qualquer implementação.**

---

## Arquitetura Atual do iadev

```
/opt/wks/dbo/iadev/
├── src/iadev/
│   ├── cli.py                    # Entry point: iadev (gate commands)
│   ├── devops.py                 # Entry point: iadev (git + PR ops)
│   ├── git_askpass.py            # HTTPS auth helper
│   ├── config/
│   │   ├── schema.py             # Dataclasses: WorkspaceConfig, RepoConfig, etc.
│   │   ├── loader.py             # TOML config loader
│   │   └── discovery.py          # Workspace discovery by CWD
│   ├── core/
│   │   ├── errors.py             # MCPError, StateError, ProcessError, etc.
│   │   ├── state.py              # .state.json: load/save, advance_stage, etc.
│   │   ├── fs.py                 # read/write JSON, text
│   │   ├── process.py            # run_command() com redaction
│   │   ├── security.py           # Redação de tokens/secrets
│   │   ├── hashing.py            # SHA256
│   │   └── logging_utils.py      # Logger com redaction
│   ├── git/
│   │   ├── operations.py         # create_branch, push_branch, pull_branch, etc.
│   │   ├── branch_parser.py      # Parse "Branch de Trabalho" do plano
│   │   └── auth/                 # GitAuthProvider (token, ssh_rsa, ssh_ed25519)
│   └── platform/
│       ├── base.py               # PlatformProvider ABC, PRResult dataclass
│       ├── azure.py              # Azure DevOps (az CLI)
│       ├── github.py             # GitHub (PyGithub)
│       └── gitlab.py             # GitLab (python-gitlab)
├── pyproject.toml
└── tests/
```

### Padrões Existentes que DEVEM ser seguidos

1. **State como fonte de verdade** — Toda operação lê, modifica e salva `.state.json`
2. **Dry-run** — Todo comando aceita `--dry-run` e loga "WOULD RUN: ..." sem executar
3. **Security** — Toda saída de subprocess é redacted; tokens nunca logados
4. **Multi-platform** — Operações de PR via `PlatformProvider` ABC (Azure/GitHub/GitLab)
5. **Logging** — Tudo via `Logger` com redaction, logs em `<demand>/logs/`
6. **Error hierarchy** — Erros tipados: `StateError`, `ProcessError`, `ValidationError`, etc.
7. **Auth** — Git remoto via `GitAuthProvider` (HTTPS + GIT_ASKPASS ou SSH)
8. **Workspace discovery** — `--workspace <NAME>` ou auto-detect por CWD

### Configuração Atual do Workspace Optum (config.toml)

```toml
[workspaces.optum.repos.lifesupport-api]
dir = "repos/lifesupport-api"
base_branch = "OG-GLOBAL"
pr_targets = ["OG-GLOBAL"]
primary = true

[workspaces.optum.repos.optumsupport-be]
dir = "repos/optum-support-be"
base_branch = "OG-GLOBAL"
pr_targets = ["OG-GLOBAL"]
primary = true

[workspaces.optum.repos.optumsupport-fe]
dir = "repos/optum-support-fe"
base_branch = "OG-GLOBAL"
pr_targets = ["OG-GLOBAL"]
primary = true

[workspaces.optum.repos.providers-back-end]
dir = "repos/providers-back-end"
base_branch = "OG-GLOBAL"
pr_targets = ["OG-GLOBAL"]
primary = false

[workspaces.optum.repos.providers-front-end]
dir = "repos/providers-front-end"
base_branch = "OG-GLOBAL"
pr_targets = ["OG-GLOBAL"]
primary = false
```

> Todos os repos: base_branch = `OG-GLOBAL`, pr_targets = `["OG-GLOBAL"]`. A integração com `desenv` é um processo separado (ADR-06 §6-7).

---

## O Que Implementar

### Resumo dos novos comandos

| Comando | Escopo | ADR-06 |
|---|---|---|
| `iadev integrate-desenv` | Integração diária OG-GLOBAL → desenv | §6 |
| `iadev prepare-merge-conflicts` | Preparar branch de resolução de conflitos OG-GLOBAL → desenv | §7 |
| `iadev finish-merge-conflicts` | Finalizar resolução, push e criar PR → desenv | §7 |
| `iadev solve-conflict <JIRA-KEY>` | Iniciar rebase de feature branch sobre OG-GLOBAL | §5.3 |
| `iadev conflict-solved <JIRA-KEY>` | (já existe) Adaptar para incluir `--force-with-lease` pós-rebase | §5.3 |

---

## Especificação Detalhada

### 1. `iadev integrate-desenv [--repo <REPO>]`

**Referência:** ADR-06 §6 — Integração Diária: OG-GLOBAL → desenv

**Propósito:** Criar PR de OG-GLOBAL → desenv para cada repositório que tenha commits novos.

**Este comando NÃO depende de JIRA-KEY** — é uma operação de manutenção do repositório, independente de demandas.

**Fluxo:**

```
1. Para cada repo (ou --repo específico):
   a. git fetch origin
   b. git log --oneline origin/desenv..origin/OG-GLOBAL
   c. Se não houver commits novos → skip (logar: "Repo <REPO>: desenv já está atualizado")
   d. Se houver commits → criar PR via platform:
      - source_branch: OG-GLOBAL
      - target_branch: desenv
      - title: "Integração diária OG-GLOBAL → desenv (YYYY-MM-DD) - <REPO>"
      - description: "Integração automática. Commits incluídos:\n<lista de commits>"
      - reviewers: do config (AZURE_DEVOPS_REVIEWERS)
   e. Verificar merge_status do PR criado
   f. Se has_conflict=true → logar WARNING e orientar para §7
   g. Se has_conflict=false → logar sucesso
2. Exibir resumo: repos integrados, PRs criados, conflitos detectados
```

**Saída esperada (exemplo):**

```
=== Integração Diária OG-GLOBAL → desenv (2026-03-09) ===
lifesupport-api:    PR #1234 criado (sem conflitos)
optumsupport-be:    Nenhum commit novo — skip
optumsupport-fe:    PR #1235 criado (⚠ CONFLITOS DETECTADOS)
providers-back-end: Nenhum commit novo — skip
providers-front-end: Nenhum commit novo — skip

Repos com conflito: optumsupport-fe
→ Execute: iadev prepare-merge-conflicts --repo optumsupport-fe
```

**Regras:**

- NÃO altera `.state.json` de nenhuma demanda (é operação de repo, não de card)
- Se já existir um PR ativo de OG-GLOBAL → desenv no repo, **não criar outro** — apenas reportar o existente
- Funciona com `--dry-run`
- Aceita `--repo <REPO>` para executar em um único repo

---

### 2. `iadev prepare-merge-conflicts [--repo <REPO>]`

**Referência:** ADR-06 §7 — Resolução de Conflitos: Branch merge-conflicts-desenv-from-OG-GLOBAL

**Propósito:** Quando a PR de OG-GLOBAL → desenv tem conflitos, preparar a branch auxiliar `merge-conflicts-desenv-from-OG-GLOBAL` para resolução manual.

**Este comando NÃO depende de JIRA-KEY.**

**Fluxo:**

```
1. Para cada repo (ou --repo específico):
   a. git fetch origin
   b. git checkout -B merge-conflicts-desenv-from-OG-GLOBAL origin/desenv
      (Cria ou recria a branch a partir de desenv)
   c. git merge origin/OG-GLOBAL
   d. Se não houver conflitos:
      - Commit automático do merge (mensagem padrão)
      - Logar: "Merge sem conflitos em <REPO>. Execute finish-merge-conflicts."
   e. Se houver conflitos:
      - Logar: "Conflitos detectados em <REPO>. Arquivos em conflito:"
      - Listar arquivos em conflito (git diff --name-only --diff-filter=U)
      - Logar: "Resolva os conflitos manualmente, depois execute:"
      - Logar: "  cd <repo-dir>"
      - Logar: "  git add <arquivos-resolvidos>"
      - Logar: "  git commit"
      - Logar: "  iadev finish-merge-conflicts --repo <REPO>"
      - NÃO fazer commit (merge incompleto, aguardando resolução humana)
2. Exibir resumo: repos preparados, conflitos encontrados
```

**Regras:**

- Branch fixa: `merge-conflicts-desenv-from-OG-GLOBAL` (uma por repo)
- A flag `-B` garante que a branch é recriada do zero a partir de `origin/desenv`
- Funciona com `--dry-run`
- NÃO altera `.state.json`
- NÃO faz push (humano precisa resolver conflitos primeiro)

---

### 3. `iadev finish-merge-conflicts [--repo <REPO>]`

**Referência:** ADR-06 §7.3 passos 5-8

**Propósito:** Após resolução manual de conflitos, publicar a branch e criar PR para desenv.

**Este comando NÃO depende de JIRA-KEY.**

**Fluxo:**

```
1. Para cada repo (ou --repo específico):
   a. Verificar que a branch local merge-conflicts-desenv-from-OG-GLOBAL existe
   b. Verificar que está na branch correta (git rev-parse --abbrev-ref HEAD)
   c. Verificar que não há merge em andamento (checar .git/MERGE_HEAD):
      - Se .git/MERGE_HEAD existe → ERRO: "Merge incompleto. Resolva os conflitos e faça commit antes."
   d. Verificar que há commits locais não publicados:
      - Se não houver → ERRO: "Nenhum commit para publicar."
   e. git push -u origin merge-conflicts-desenv-from-OG-GLOBAL
   f. Criar PR via platform:
      - source_branch: merge-conflicts-desenv-from-OG-GLOBAL
      - target_branch: desenv
      - title: "Integração OG-GLOBAL → desenv com resolução de conflitos (YYYY-MM-DD) - <REPO>"
      - description: "Resolução de conflitos da integração OG-GLOBAL → desenv."
      - reviewers: do config
   g. Verificar merge_status do PR criado
   h. Se has_conflict=true → ERRO: "PR criado mas AINDA tem conflitos (cenário raro)."
   i. Se has_conflict=false → logar sucesso
2. Exibir resumo
```

**Regras:**

- NÃO altera `.state.json`
- Funciona com `--dry-run`
- Se já existir PR ativo de `merge-conflicts-desenv-from-OG-GLOBAL → desenv`, reportar sem criar duplicata
- NÃO limpa a branch local automaticamente (o humano decide quando limpar)

---

### 4. `iadev solve-conflict <JIRA-KEY> [--repo <REPO>]`

**Referência:** ADR-06 §5.3 — Resolução de Conflitos em Feature Branches (Rebase)

**Propósito:** Quando um PR de feature → OG-GLOBAL tem conflitos, iniciar o rebase da feature sobre OG-GLOBAL para resolução manual.

**Este comando depende de JIRA-KEY** — opera sobre as branches de feature da demanda.

**Pré-condições:**

- `.state.json` existe e `stage` ≥ `prs_created` ou `conflict-resolution`
- Pelo menos um repo em `state["prs"]` com `has_conflict=true`

**Fluxo:**

```
1. Carregar .state.json
2. Identificar repos com conflitos:
   - Filtrar state["prs"] onde has_conflict=true
   - Se --repo especificado, filtrar somente esse
   - Se nenhum conflito encontrado → ERRO: "Nenhum PR com conflito encontrado."
3. Para cada repo com conflito:
   a. Identificar a branch de feature: state["repos"][repo]["branch"]
   b. git fetch origin
   c. git checkout <feature-branch>
   d. git rebase origin/OG-GLOBAL
   e. Se não houver conflitos:
      - Logar: "Rebase sem conflitos em <REPO>."
      - Logar: "Execute: iadev conflict-solved <JIRA-KEY> --repo <REPO>"
   f. Se houver conflitos:
      - Logar: "Conflitos detectados durante rebase em <REPO>. Arquivos:"
      - Listar arquivos em conflito
      - Logar instruções para o humano:
        "  Resolva os conflitos manualmente, depois execute:"
        "    git add <arquivos-resolvidos>"
        "    git rebase --continue"
        "  Repita até o rebase estar completo."
        "  Depois execute: iadev conflict-solved <JIRA-KEY> --repo <REPO>"
      - PARAR neste repo (não avançar para o próximo — conflitos pendentes)
4. Registrar no commands_log: "solve-conflict <JIRA-KEY>"
5. Salvar .state.json
```

**Regras:**

- NÃO faz push (humano resolve, depois `conflict-solved` faz push)
- NÃO avança stage
- Se o rebase conflitar, para imediatamente e instrui o humano
- Funciona com `--dry-run`
- Apenas um repo por vez quando há conflitos (o humano precisa resolver antes de seguir)

---

### 5. Adaptar `iadev conflict-solved <JIRA-KEY>` (já existe)

**Referência:** ADR-06 §5.3.2 passo 3

**Adaptação necessária:** O comando existente precisa suportar o cenário de rebase, onde o push requer `--force-with-lease` em vez de push normal.

**Mudanças:**

```
1. Ao verificar repos com PRs de feature → OG-GLOBAL (não integração diária):
   a. Verificar que NÃO há rebase em andamento:
      - Checar existência de .git/rebase-merge/ ou .git/rebase-apply/
      - Se existir → ERRO: "Rebase em andamento em <REPO>. Finalize antes de continuar."
   b. Usar git push --force-with-lease (em vez de git push normal):
      - Necessário porque o rebase reescreveu o histórico da branch
   c. Após push, verificar PR via platform.get_pr_status():
      - Se sem conflito → marcar repo como done
      - Se ainda com conflito → reportar (OG-GLOBAL recebeu novos commits)
2. Registrar no commands_log: "conflict-solved <JIRA-KEY>"
3. Se todos os repos resolvidos → avançar stage para done
```

**Nova função em git/operations.py:**

```python
def force_push_branch(repo_name, branch_name, *, state, workspace, auth, dry_run=False, logger=None):
    """Push com --force-with-lease (pós-rebase de feature branch)."""
    require_stage(state, "change_approved")
    repo_dir = Path(workspace.root) / workspace.repos[repo_name].dir
    _run_git_remote(
        ["push", "--force-with-lease", "origin", branch_name],
        repo_dir=repo_dir,
        auth=auth,
        dry_run=dry_run,
        logger=logger,
    )
```

**Detecção do tipo de conflito:**

- Se o PR é feature → OG-GLOBAL → usar `force_push_branch` (pós-rebase)
- Se o PR é OG-GLOBAL → desenv → NÃO aplicável (integração usa fluxo separado)

Para detectar: verificar `target_branch` do PR em `state["prs"]`:
- Se `target_branch == "OG-GLOBAL"` → feature PR → force push
- Se `target_branch == "desenv"` → integração PR → fluxo separado (finish-merge-conflicts)

---

## Mudanças nos Módulos Existentes

### git/operations.py — Novas funções

```python
def fetch_origin(repo_name, *, workspace, auth, dry_run=False, logger=None):
    """git fetch origin"""

def rebase_on_base(repo_name, base_branch, *, workspace, auth, dry_run=False, logger=None):
    """git rebase origin/<base_branch>. Retorna (success: bool, conflicts: list[str])."""

def force_push_branch(repo_name, branch_name, *, state, workspace, auth, dry_run=False, logger=None):
    """git push --force-with-lease origin <branch>"""

def checkout_branch(repo_name, branch_name, *, workspace, dry_run=False, logger=None):
    """git checkout <branch> (branch existente)"""

def checkout_new_branch_from_remote(repo_name, branch_name, remote_base, *, workspace, auth, dry_run=False, logger=None):
    """git checkout -B <branch_name> origin/<remote_base>"""

def merge_remote_branch(repo_name, remote_branch, *, workspace, auth, dry_run=False, logger=None):
    """git merge origin/<remote_branch>. Retorna (success: bool, conflicts: list[str])."""

def has_pending_rebase(repo_name, *, workspace) -> bool:
    """Checa se .git/rebase-merge/ ou .git/rebase-apply/ existem."""

def has_pending_merge(repo_name, *, workspace) -> bool:
    """Checa se .git/MERGE_HEAD existe."""

def get_conflict_files(repo_name, *, workspace) -> list[str]:
    """git diff --name-only --diff-filter=U"""

def log_diff(repo_name, from_ref, to_ref, *, workspace, dry_run=False, logger=None) -> list[str]:
    """git log --oneline <from_ref>..<to_ref>. Retorna lista de linhas."""
```

### platform/base.py — Verificar PRs existentes

Adicionar ao `PlatformProvider` ABC:

```python
def list_prs(self, *, repo_name, source_branch=None, target_branch=None,
             status="active", dry_run=False, logger=None) -> list[PRResult]:
    """Listar PRs ativos filtrados por source/target."""
```

> Nota: `list_prs` já existe na base. Verificar se os filtros são suficientes. Se não, adaptar.

### cli.py — Novo comando

Adicionar `solve-conflict` como subcommand de `iadev`.

### devops.py — Novos comandos

Adicionar como subcommands de `iadev`:
- `integrate-desenv`
- `prepare-merge-conflicts`
- `finish-merge-conflicts`

---

## Testes

Para cada novo comando, criar testes em `tests/`:

1. **test_integrate_desenv.py**
   - `test_skip_when_no_new_commits` — nenhum commit novo, nenhum PR criado
   - `test_create_pr_when_diff_exists` — commits novos → PR criado
   - `test_detect_conflict_on_created_pr` — PR criado com conflitos → warning
   - `test_skip_existing_active_pr` — PR já existe → reportar sem duplicar
   - `test_dry_run` — nenhuma operação real executada

2. **test_prepare_merge_conflicts.py**
   - `test_checkout_from_desenv` — branch criada a partir de origin/desenv
   - `test_merge_no_conflicts` — merge sem conflitos → commit automático
   - `test_merge_with_conflicts` — conflitos → lista arquivos e instrui humano
   - `test_dry_run`

3. **test_finish_merge_conflicts.py**
   - `test_push_and_create_pr` — push + PR criado
   - `test_error_on_pending_merge` — MERGE_HEAD presente → erro
   - `test_error_on_no_commits` — nada para publicar → erro
   - `test_skip_existing_pr` — PR já existe → reportar
   - `test_dry_run`

4. **test_solve_conflict.py**
   - `test_rebase_no_conflicts` — rebase limpo → instruir para conflict-solved
   - `test_rebase_with_conflicts` — conflitos → lista e para
   - `test_no_conflicting_prs` — nenhum PR com conflito → erro
   - `test_dry_run`

5. **test_conflict_solved_rebase.py** (adaptar existentes)
   - `test_force_push_after_rebase` — usa --force-with-lease
   - `test_error_on_pending_rebase` — rebase em andamento → erro
   - `test_verify_pr_after_push` — verifica status do PR pós-push

---

## Fluxo Completo — Cenários de Uso

### Cenário A: Integração diária sem conflitos

```bash
iadev integrate-desenv
# → Cria PRs OG-GLOBAL → desenv para repos com commits novos
# → Todos sem conflito
# → Humano aprova e faz merge no Azure DevOps
```

### Cenário B: Integração diária com conflitos

```bash
iadev integrate-desenv
# → optumsupport-fe: PR criado com CONFLITOS

iadev prepare-merge-conflicts --repo optumsupport-fe
# → Cria branch merge-conflicts-desenv-from-OG-GLOBAL
# → Merge origin/OG-GLOBAL → conflitos detectados
# → Lista arquivos em conflito

# Humano resolve manualmente:
cd repos/optum-support-fe
# edita arquivos...
git add .
git commit

iadev finish-merge-conflicts --repo optumsupport-fe
# → Push merge-conflicts-desenv-from-OG-GLOBAL
# → Cria PR → desenv
# → Humano aprova no Azure DevOps
```

### Cenário C: Feature PR com conflitos (rebase)

```bash
# PR OG-123-feature → OG-GLOBAL detecta conflitos
iadev solve-conflict OG-123 --repo lifesupport-api
# → Faz rebase da branch OG-123-feature sobre origin/OG-GLOBAL
# → Conflitos detectados → lista arquivos

# Humano resolve:
cd repos/lifesupport-api
# edita arquivos...
git add .
git rebase --continue
# repete se necessário...

iadev conflict-solved OG-123
# → git push --force-with-lease (detecta que é feature→OG-GLOBAL)
# → Verifica PR — sem conflitos
# → Avança stage → done
```

---

## Restrições e Regras

1. **Nunca** fazer push direto em `OG-GLOBAL`, `desenv` ou `master`
2. **Nunca** logar tokens, secrets ou credenciais
3. **Nunca** usar `--force` (sempre `--force-with-lease`)
4. **Nunca** fazer merge automático de PR (humano aprova no Azure DevOps)
5. Comandos de integração (`integrate-desenv`, `prepare-merge-conflicts`, `finish-merge-conflicts`) são **independentes de JIRA-KEY**
6. Comandos de feature (`solve-conflict`, `conflict-solved`) dependem de JIRA-KEY e do `.state.json`
7. Todos os comandos devem funcionar com `--dry-run`
8. Todos os comandos devem logar via `Logger` com redaction
9. Seguir a hierarquia de erros existente (`MCPError`, `StateError`, `ProcessError`, etc.)
10. Autenticação via `GitAuthProvider` existente (HTTPS + GIT_ASKPASS)

---

## Documento de Referência

O fluxo Git completo está documentado no ADR-06:
`/opt/wks/csptech/optum/docs/ADR/ADR-06-fluxo-git-operacional.md`

Seções relevantes:
- §5 — Fluxo de desenvolvimento de features
- §5.3 — Resolução de conflitos em feature branches (rebase)
- §6 — Integração diária OG-GLOBAL → desenv
- §7 — Resolução de conflitos: branch merge-conflicts-desenv-from-OG-GLOBAL
- §8 — Fluxo completo (visão geral)
