# Design — Runtime data-driven + abstração de orquestrador

- **Data:** 2026-05-30
- **Status:** Aprovado (brainstorming)
- **Versão base:** dop 0.5.0
- **Escopo:** subsistema `src/dop/runtime/` + `src/dop/config/`

## 1. Problema

O subsistema `runtime/` do `dop` tem conhecimento **hard-coded** específico do
workspace Optum: nomes de apps, portas, mapas de URL de back-end, ordem de suites
E2E, comandos de build de front-end, nomes de volumes e o prefixo de projeto Docker
(`optum-dev_`). Além disso, a interação com containers está acoplada diretamente ao
`docker compose`. Isso impede reuso em outros workspaces e em outros orquestradores.

Locais do acoplamento atual:
- `runtime/resolve.py`: `_BE_URL_MAP` (5 back-ends, env vars, portas).
- `runtime/e2e.py`: `E2E_SUITE_ORDER` (3 suites FE).
- `runtime/handlers.py`: `_SUITE_FE_PORT`, `_SUITE_BE_PORT`, `_FE_BUILD_COMMANDS`,
  alvos de `clean` e o prefixo `optum-dev_`.
- `cli.py`: `DESENV_BRANCH`/`MERGE_CONFLICTS_BRANCH` (fora deste escopo — é fluxo Git).

## 2. Objetivos

1. **Runtime data-driven** via `config.toml`, como os repos já são.
2. **Descoberta de containers** a partir do config, não de constantes.
3. **Abstração de orquestrador** (provider pattern, espelhando `platform/`):
   `docker_compose` implementado agora; `kubernetes`/`okd`/`rancher` selecionáveis
   por config no futuro. O backend é escolhido no `config.toml`.

## 3. Decisões (do brainstorming)

| # | Decisão |
|---|---|
| D1 | **Fonte de verdade:** apps lógicos no config mapeados para serviços já definidos no `docker-compose.yml`. O compose continua dono de imagem/build/volumes/rede. |
| D2 | **Escopo do provider:** ciclo de vida completo **+ `run_ephemeral`**. E2E, codegen e clean roteiam pelo provider. A lógica de e2e (portas/URLs/run number) fica no handler. |
| D3 | **Breaking change OK:** sem fallback de constantes. Campo obrigatório ausente → `ValidationError`. O `config.toml` real do Optum é atualizado como parte do trabalho. |
| D4 | **E2E derivado dos apps:** um app FE declara `e2e_suite`; `E2E_BASE_URL` vem da porta do FE, `E2E_API_URL` da porta do BE em `depends_on`; ordem das suites = ordem dos apps FE no config. |
| D5 | **Subseção por backend:** `[runtime]` genérico + `[runtime.docker_compose]` com knobs específicos. Futuro: `[runtime.kubernetes]`. |

## 4. Schema de configuração

### 4.1 Genérico `[runtime]`
```toml
[workspaces.optum.runtime]
orchestrator = "docker_compose"     # seletor; só docker_compose implementado
infra = ["mongodb", "allure"]       # serviços lógicos de infra sempre no ar
default_max_strikes = 3
```

### 4.2 App lógico `[runtime.apps.<nome>]` (agnóstico de backend)
```toml
[workspaces.optum.runtime.apps.optum-support-fe]
service    = "optum-support-fe"     # serviço no docker-compose.yml (mapeamento)
role       = "frontend"            # frontend | backend
port       = 5173                  # porta servida (E2E_BASE_URL / URLs)
aliases    = ["osf"]
depends_on = ["optum-support-be"]  # deps lógicas (FE→BE); substitui [[fe_deps]]
e2e_suite  = "optum-support-fe"    # diretório em e2e/; presença = é suite E2E
[workspaces.optum.runtime.apps.optum-support-fe.build]
dir      = "repos/optum-support-fe"   # build no host (nginx serve o dist)
command  = "npx vite build"
artifact = "dist"

[workspaces.optum.runtime.apps.optum-support-be]
service          = "optum-support-be"
role             = "backend"
port             = 8080
aliases          = ["osb"]
url_env          = "OPTUM_SUPPORT_BE_URL"        # injeta URL p/ outros apps
fallback_url_env = "AZURE_OPTUM_SUPPORT_BE_URL"  # fallback quando BE fora do ar
```

Campos por app:

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `service` | str | sim | Nome do serviço no `docker-compose.yml`. |
| `role` | `frontend`\|`backend` | sim | Papel do app. |
| `port` | int | sim | Porta servida (URLs/E2E). |
| `aliases` | list[str] | não | Aliases curtos. |
| `depends_on` | list[str] | não | Apps dos quais depende (auto-start). |
| `url_env` | str | não (BE) | Env var onde injetar a URL deste BE. |
| `fallback_url_env` | str | não (BE) | Env var de fallback (Azure) quando o BE não está no ar. |
| `e2e_suite` | str | não (FE) | Nome do diretório da suite em `e2e/`. |
| `build.dir` / `build.command` / `build.artifact` | str | não (FE) | Build no host. |
| `debug_port` | int | não | Porta de debug (preservado do schema atual). |

### 4.3 Específico do backend `[runtime.docker_compose]`
```toml
[workspaces.optum.runtime.docker_compose]
compose_file = "docker-compose.yml"
env_files    = ["docker/.env", "docker/.env.runtime"]
project_name = "optum-dev"          # prefixo de volume (era hardcoded)
[workspaces.optum.runtime.docker_compose.ephemeral_runner]
service = "playwright-env"
profile = "e2e"
[workspaces.optum.runtime.docker_compose.clean]   # categorias → volumes (provider prefixa project_name)
maven        = ["m2-cache"]
allure       = ["allure-results", "allure-reports"]
node_modules = ["optum-fe-node_modules", "providers-fe-node_modules", "canal-fe-node_modules"]
be_target    = ["lifesupport-target", "optum-be-target", "providers-be-target", "canal-be-target"]
```

### 4.4 Dataclasses (`config/schema.py`)

- `AppConfig` estendida: `service`, `role`, `port`, `aliases`, `depends_on`,
  `url_env`, `fallback_url_env`, `e2e_suite`, `build: AppBuildConfig | None`,
  `debug_port`. Remover `kind`/`dev_cmd`/`lifesupport_url_env`/`repo` (substituídos).
- Nova `AppBuildConfig`: `dir`, `command`, `artifact`.
- `RuntimeConfig` estendida: `orchestrator: str = "docker_compose"`,
  `infra: list[str]`, `apps: dict[str, AppConfig]`, `aliases: dict[str,str]`
  (derivado), `default_max_strikes`, `docker_compose: DockerComposeConfig | None`.
  Remover `compose_file`/`env_file`/`compose_timeout`/`fe_deps` do nível genérico
  (migram para a subseção/`depends_on`).
- Nova `DockerComposeConfig`: `compose_file`, `env_files: list[str]`,
  `project_name`, `ephemeral_runner: EphemeralRunnerConfig`,
  `clean: dict[str, list[str]]`.
- Nova `EphemeralRunnerConfig`: `service`, `profile`.

`config/loader.py` parseia as novas seções; `aliases` é derivado de `apps[*].aliases`.

## 5. Abstração do orquestrador (`runtime/orchestrator/`)

```
src/dop/runtime/orchestrator/
├── base.py            # RuntimeProvider (ABC) + ServiceStatus (dataclass)
├── docker_compose.py  # DockerComposeProvider (usa runtime/compose.py)
└── __init__.py        # build_runtime_provider(workspace) — factory
```

### 5.1 DTO
```python
@dataclass
class ServiceStatus:
    name: str            # nome lógico do app
    service: str         # nome do serviço no backend
    state: str | None    # running | exited | ...
    health: str | None   # healthy | starting | ...
    port: int | None
    up: bool
```

### 5.2 Interface `RuntimeProvider`
```python
up(apps: list[str], *, build=False, wait=True, dry_run=False, logger=None) -> None
stop(apps: list[str], *, dry_run=False, logger=None) -> None       # [] = todos
restart(apps: list[str], *, dry_run=False, logger=None) -> None
status(*, dry_run=False, logger=None) -> list[ServiceStatus]
logs(apps, *, follow=True, tail=None, since=None, dry_run=False, logger=None) -> None
run_ephemeral(args: list[str], *, env: dict[str,str], dry_run=False, logger=None) -> int
clean(categories: list[str], *, dry_run=False, logger=None) -> None
```

- `apps` são **nomes lógicos**; o provider mapeia para `service` via config.
- `infra` é sempre incluída em `up` (o provider lê `runtime.infra`).
- `run_ephemeral` usa o `ephemeral_runner` (service+profile) do config.
- `clean(categories)` recebe categorias lógicas (`maven`, `allure`,
  `node_modules`, `be_target`, ou `all`); o provider resolve para volumes
  concretos com prefixo `project_name`.

### 5.3 Factory
`build_runtime_provider(workspace)` seleciona por `runtime.orchestrator`:
- `"docker_compose"` → `DockerComposeProvider`.
- `"kubernetes"|"okd"|"rancher"` → `ValidationError("orchestrator '<x>' ainda não
  implementado; use 'docker_compose'")`.
- desconhecido → `ValidationError`.

### 5.4 Reuso
`runtime/compose.py` (construtores de comando puros) permanece e vira a engrenagem
interna do `DockerComposeProvider`. `rebuild` **não** entra no provider: o build de
FE roda no host (handler) e em seguida chama `provider.restart()`.

## 6. Divisão de responsabilidades

- `runtime/resolve.py` — `expand_apps()` (resolve alias + auto-inclui BE de
  `depends_on`) e `infer_urls()` (URL local se BE no ar, senão `fallback_url_env`)
  passam a **ler do config**, sem constantes.
- `runtime/e2e.py` — `E2E_SUITE_ORDER` deixa de existir; a ordem/descoberta vem dos
  apps FE com `e2e_suite`. `resolve_e2e_target` (suite/JIRA/arquivo) inalterado.
- `runtime/handlers.py` — mantém a lógica de negócio (validar `.env`, checar portas
  com `lsof`, build de FE no host, computar `E2E_BASE_URL`/`E2E_API_URL` por suite a
  partir das portas do config, run numbering, escrever `.env.runtime`) e **delega ao
  provider** as operações concretas.

## 7. Tratamento de erros

- Campo obrigatório de app/seção ausente → `ValidationError` no parse (com app/campo).
- App/alias desconhecido → `ValidationError`.
- Orchestrator não implementado/desconhecido → `ValidationError` clara na factory.
- Porta ocupada → checagem `lsof` no handler antes de `provider.up`.
- `--dry-run` honrado em toda a cadeia (provider monta comando e loga `WOULD RUN`).

## 8. Testes

- `build_runtime_provider`: seleção compose; erro claro p/ k8s/okd/rancher/desconhecido.
- `DockerComposeProvider`: monta comandos corretos para up/stop/restart/status/logs/
  run_ephemeral/clean (reaproveita `test_runtime_compose.py`).
- `resolve`/`e2e` data-driven: `expand_apps`/`infer_urls`/descoberta de suites lendo
  um `WorkspaceConfig` de teste (sem constantes).
- Parse do novo `[runtime]`/`[runtime.docker_compose]` (estende `test_config_*`).
- Atualizar testes que dependiam das constantes removidas.
- Padrão de teste: `dry_run=True` + asserção sobre os comandos gerados.

## 9. Escopo

**Inclui:** schema novo (`config/schema.py`, `loader.py`); subpacote
`runtime/orchestrator/`; refatoração de `resolve/e2e/handlers`; remoção das
constantes Optum; `config.toml` exemplo versionado (`docs/examples/`) + atualização
do `$DOP_CONFIG` real; atualização de docs (`reference/configuration.md`, ADR-0011/
0012, novo ADR da abstração de orquestrador, PRD-0004).

**Fora de escopo (YAGNI):** implementações reais de k8s/okd/rancher (só seletor +
erro); geração de compose/manifests; mudanças em `git/`/`platform/`/`core/`; o
acoplamento de branches Git em `cli.py` (fluxo separado).

## 10. Migração

1. Estender schema + loader (com validação) — testes primeiro.
2. Criar `orchestrator/` (base + factory + docker_compose) sobre `compose.py`.
3. Tornar `resolve`/`e2e` data-driven.
4. Refatorar `handlers` para delegar ao provider; remover constantes.
5. Atualizar `config.toml` (exemplo + real) e a documentação.
6. Rodar a suíte completa; ajustar testes impactados.
