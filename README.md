# dop-cmd

DevOps Pipeline CLI - IA-First development workflow, multi-workspace, multi-platform.

Ferramenta operacional do DOP: **responde pelo ambiente**. Acessa a workspace
diretamente e executa operações git, PRs multi-plataforma, runtime docker-compose,
testes e2e/AAA e relatórios Allure.

> **Projeto próprio.** Esta ferramenta viveu até agosto de 2026 no repositório
> `dop-cli`. O nome foi devolvido a outro projeto, e ela passou a ter repositório e
> ciclo de vida independentes.
>
> A distribuição chama-se `dop-cmd`, mas o pacote Python e o executável continuam
> sendo `dop`. Por isso ela **não pode coexistir** com qualquer outro pacote que
> instale o comando `dop` no mesmo ambiente Python.

## Install

```bash
pip install git+ssh://git@github.com/Digital-Business-One/dop-cmd.git
```

## Configuration

Create `~/.config/dop/config.toml` (or set `DOP_CONFIG` to a custom path):

```toml
[workspaces.optum]
root = "/opt/wks/csptech/optum"
demands_dir = "docs/RFC"
platform = "azure_devops"
auth_method = "token"

[workspaces.optum.credentials]
login_env = "GIT_OPTUM_LOGIN"
token_env = "GIT_OPTUM_TOKEN"

[workspaces.optum.platform_config]
org_env = "AZURE_DEVOPS_ORG"
project_env = "AZURE_DEVOPS_PROJECT"
reviewers_env = "AZURE_DEVOPS_REVIEWERS"

[workspaces.optum.pr_doc_suffix_map]
"lifesupport-api"    = "lifesupport-api"
"optumsupport-be"    = "optum-support-be"
"optumsupport-fe"    = "optum-support-fe"
"providers-back-end" = "providers-back-end"
"providers-front-end"= "providers-front-end"

[workspaces.optum.repos.lifesupport-api]
dir         = "repos/lifesupport-api"
base_branch = "OG-GLOBAL"
pr_targets  = ["OG-GLOBAL"]
primary     = true

# ... other repos
```

## CLI Commands

### Gate commands

```bash
dop context-approved OG-123
dop plan-approved OG-123
dop build-passed OG-123
dop change-approved OG-123
dop conflict-solved OG-123
dop rerun plan OG-123
dop reset OG-123 --to plan_generated
dop --dry-run change-approved OG-123
```

### DevOps commands

```bash
dop-devops git-pull OG-123
dop-devops git-push OG-123
dop-devops pr-create OG-123 --summary "Short summary"
dop-devops pr-publish OG-123
```

### Integration commands

```bash
dop integrate-desenv
dop integrate-desenv --repo lifesupport-api
dop prepare-merge-conflicts --repo optumsupport-fe
dop finish-merge-conflicts --repo optumsupport-fe
```

### Feature conflict resolution

```bash
dop solve-conflict OG-123 --repo lifesupport-api
dop conflict-solved OG-123
```

## Plan branch table requirement

The `plan-approved` command reads `02-plan-00.md` and expects this section:

```markdown
## Branch de Trabalho

| Repo | Branch |
|---|---|
| lifesupport-api | OG-123-my-change |
| optumsupport-be | OG-123-my-change |
```

Repos omitted from the table are marked as `skipped`.

## Multi-platform support

Supported platforms:

- Azure DevOps
- GitHub
- GitLab

## Auth methods

Supported auth methods:

- `token` (HTTPS + GIT_ASKPASS)
- `ssh_rsa`
- `ssh_ed25519`

For GitHub and GitLab APIs, use tokens via `credentials.token_env`.
