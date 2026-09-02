# Referência — Configuração (`config.toml`)

> Fonte de verdade: `src/dop/config/schema.py` (dataclasses) + `loader.py`. v0.5.0.

- **Caminho:** `$DOP_CONFIG` ou `~/.config/dop/config.toml`.
- **Formato:** TOML, parseado por `tomllib`. Campos opcionais usam *defaults*.
- **Seleção de workspace:** `--workspace <nome>` ou auto-detecção pelo CWD (o `root`
  do workspace deve ser ancestral do diretório atual).

## Estrutura

```toml
[workspaces.<nome>]
root = "/opt/wks/csptech/optum"          # obrigatório
demands_dir = "docs/RFC"                  # default
platform = "azure_devops"                 # azure_devops | github | gitlab
auth_method = "token"                     # token | ssh_rsa | ssh_ed25519
jira_base_url = "https://atlassian.net/browse"          # default
jira_key_pattern = "^[A-Z][A-Z0-9]+-\\d+$"              # default
pr_doc_prefix = "99-pr-00"                # default
long_branches = ["master","main","desenv","hml","OG-GLOBAL"]   # default

[workspaces.<nome>.credentials]
login_env = "GIT_OPTUM_LOGIN"   # var de ambiente com o usuário (HTTPS)
token_env = "GIT_OPTUM_TOKEN"   # var com o token/PAT
ssh_key_env = "GIT_SSH_KEY"     # var com o caminho da chave SSH (auth ssh_*)

[workspaces.<nome>.platform_config]
org_env = "AZURE_DEVOPS_ORG"           # Azure: var com a org
project_env = "AZURE_DEVOPS_PROJECT"   # Azure: var com o projeto
reviewers_env = "AZURE_DEVOPS_REVIEWERS"  # Azure: var com revisores (csv/space)
org = "minha-org"                      # GitHub: org (valor direto)
gitlab_url = "https://gitlab.com"      # GitLab: URL da instância (default)
namespace = "meu-grupo"                # GitLab: namespace/grupo

[workspaces.<nome>.pr_doc_suffix_map]   # sufixo do título de PR por repo (Azure)
"optumsupport-be" = "optum-support-be"

[workspaces.<nome>.repos.<repo>]
dir = "repos/<repo>"            # obrigatório (relativo a root)
base_branch = "OG-GLOBAL"       # obrigatório
pr_targets = ["OG-GLOBAL"]      # obrigatório
primary = true                  # default true
azure_org = "..."               # override de org Azure por repo (opcional)
azure_project = "..."           # override de projeto Azure por repo (opcional)
long_branches = ["..."]         # override das branches longas (opcional)

# ─── Runtime (data-driven; ver ADR-0011 e ADR-0014) ───
[workspaces.<nome>.runtime]
orchestrator = "docker_compose"   # docker_compose | (futuro: kubernetes/okd/rancher)
infra = ["mongodb", "allure"]     # serviços lógicos de infra sempre no ar
default_max_strikes = 3           # default (1..10 no e2e)

# App lógico → serviço do docker-compose.yml
[workspaces.<nome>.runtime.apps.<app>]
service = "optum-support-fe"   # nome do serviço no docker-compose.yml (obrigatório)
role = "frontend"              # frontend | backend (obrigatório)
port = 5173                    # porta servida / E2E (obrigatório)
aliases = ["osf"]              # aliases curtos (alimentam runtime.aliases)
depends_on = ["optum-support-be"]  # deps lógicas (auto-start)
url_env = "OPTUM_SUPPORT_BE_URL"           # (BE) env var p/ injetar a URL
fallback_url_env = "AZURE_OPTUM_SUPPORT_BE_URL"  # (BE) fallback quando fora do ar
e2e_suite = "optum-support-fe"             # (FE) diretório em e2e/
debug_port = 5005                          # opcional
[workspaces.<nome>.runtime.apps.<app>.build]   # (FE) build no host
dir = "repos/optum-support-fe"
command = "npx vite build"
artifact = "dist"

# Específico do backend de orquestração
[workspaces.<nome>.runtime.docker_compose]
compose_file = "docker-compose.yml"
env_files = ["docker/.env", "docker/.env.runtime"]
project_name = "optum-dev"                 # prefixo de volume
[workspaces.<nome>.runtime.docker_compose.ephemeral_runner]
service = "playwright-env"
profile = "e2e"
[workspaces.<nome>.runtime.docker_compose.clean]   # categoria → volumes
maven = ["m2-cache"]
allure = ["allure-results", "allure-reports"]
node_modules = ["optum-fe-node_modules"]
```

> Exemplo completo e validado: [`docs/examples/config.optum.toml`](../examples/config.optum.toml).

## Dataclasses (resumo)

| Dataclass | Campos |
|---|---|
| `WorkspaceConfig` | `name, root, demands_dir, platform, auth_method, jira_base_url, jira_key_pattern, credentials, platform_config, repos, pr_doc_prefix, pr_doc_suffix_map, long_branches, runtime` |
| `RepoConfig` | `name, dir, base_branch, pr_targets, primary=True, azure_org=None, azure_project=None, long_branches=None` |
| `CredentialsConfig` | `login_env, token_env, ssh_key_env` |
| `PlatformConfig` | `org_env, project_env, reviewers_env, org, gitlab_url="https://gitlab.com", namespace` |
| `RuntimeConfig` | `orchestrator="docker_compose", infra[], default_max_strikes=3, apps{}, aliases{}, docker_compose=None` |
| `AppConfig` | `name, service, role, port, aliases=[], depends_on=[], url_env=None, fallback_url_env=None, e2e_suite=None, build=None, debug_port=None` |
| `AppBuildConfig` | `dir, command, artifact="dist"` |
| `DockerComposeConfig` | `compose_file, env_files[], project_name="", ephemeral_runner=None, clean{}` |
| `EphemeralRunnerConfig` | `service, profile=None` |

> **Segredos não vão no TOML** — apenas os *nomes* das variáveis de ambiente
> (`*_env`). Os valores reais vivem no ambiente do processo
> (ver [ADR-0006](../adr/0006-redacao-de-segredos.md)).
