# Runtime Orchestrator Abstraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar o subsistema `runtime/` do dop data-driven via `config.toml` e abstrair o orquestrador atrás de um provider pattern (docker_compose agora; k8s/okd/rancher futuros), removendo todas as constantes hard-coded do Optum.

**Architecture:** Apps lógicos descritos em `[runtime.apps.*]` mapeiam para serviços do `docker-compose.yml`. Um `RuntimeProvider` (espelhando `platform/PlatformProvider`) encapsula a interação com o backend; `build_runtime_provider(ws)` seleciona por `runtime.orchestrator`. Handlers mantêm a lógica de negócio (build de FE no host, URLs de e2e, run numbering) e delegam operações concretas ao provider.

**Tech Stack:** Python 3.11, dataclasses, tomllib, pytest/unittest, docker compose.

**Spec:** `docs/superpowers/specs/2026-05-30-runtime-orchestrator-design.md`

**Test runner:** `python -m pytest <path> -v` a partir de `/opt/wks/dbo/dop`.

---

## Task 1: Novos dataclasses de configuração (`config/schema.py`)

**Files:**
- Modify: `src/dop/config/schema.py`
- Test: `tests/test_config_schema_v05.py`

- [ ] **Step 1: Reescrever o teste de schema (falhará)**

Substituir TODO o conteúdo de `tests/test_config_schema_v05.py` por:

```python
from dop.config.schema import (
    RuntimeConfig, AppConfig, AppBuildConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
    WorkspaceConfig,
)


def test_app_config_minimal():
    app = AppConfig(name="optum-support-be", service="optum-support-be",
                    role="backend", port=8080)
    assert app.aliases == []
    assert app.depends_on == []
    assert app.url_env is None
    assert app.fallback_url_env is None
    assert app.e2e_suite is None
    assert app.build is None
    assert app.debug_port is None


def test_app_build_config():
    b = AppBuildConfig(dir="repos/optum-support-fe", command="npx vite build")
    assert b.artifact == "dist"


def test_runtime_config_defaults():
    rc = RuntimeConfig()
    assert rc.orchestrator == "docker_compose"
    assert rc.infra == []
    assert rc.default_max_strikes == 3
    assert rc.apps == {}
    assert rc.aliases == {}
    assert rc.docker_compose is None


def test_docker_compose_config_defaults():
    dc = DockerComposeConfig()
    assert dc.compose_file == "docker-compose.yml"
    assert dc.env_files == ["docker/.env"]
    assert dc.project_name == ""
    assert dc.ephemeral_runner is None
    assert dc.clean == {}


def test_ephemeral_runner_config():
    er = EphemeralRunnerConfig(service="playwright-env", profile="e2e")
    assert er.service == "playwright-env"
    assert er.profile == "e2e"


def test_workspace_config_has_runtime():
    ws = WorkspaceConfig(name="test", root="/tmp/test")
    assert isinstance(ws.runtime, RuntimeConfig)
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_config_schema_v05.py -v`
Expected: FAIL (ImportError: cannot import name 'AppBuildConfig').

- [ ] **Step 3: Reescrever `src/dop/config/schema.py`**

Substituir TODO o conteúdo por:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepoConfig:
    name: str
    dir: str
    base_branch: str
    pr_targets: list[str]
    primary: bool = True
    azure_org: str | None = None
    azure_project: str | None = None
    long_branches: list[str] | None = None  # override; se None, usa workspace.long_branches


@dataclass
class CredentialsConfig:
    login_env: str | None = None
    token_env: str | None = None
    ssh_key_env: str | None = None


@dataclass
class PlatformConfig:
    org_env: str | None = None
    project_env: str | None = None
    reviewers_env: str | None = None
    org: str | None = None
    gitlab_url: str = "https://gitlab.com"
    namespace: str | None = None


@dataclass
class AppBuildConfig:
    """Build de front-end no host (nginx serve o artefato)."""
    dir: str
    command: str
    artifact: str = "dist"


@dataclass
class AppConfig:
    """App lógico mapeado para um serviço do orquestrador."""
    name: str
    service: str               # nome do serviço no docker-compose.yml
    role: str                  # "frontend" | "backend"
    port: int
    aliases: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    url_env: str | None = None          # env var onde injetar a URL deste app (BE)
    fallback_url_env: str | None = None # env var de fallback (Azure) quando fora do ar
    e2e_suite: str | None = None        # diretório da suite em e2e/ (FE)
    build: AppBuildConfig | None = None
    debug_port: int | None = None


@dataclass
class EphemeralRunnerConfig:
    """Serviço usado para execuções efêmeras (e2e/codegen)."""
    service: str
    profile: str | None = None


@dataclass
class DockerComposeConfig:
    compose_file: str = "docker-compose.yml"
    env_files: list[str] = field(default_factory=lambda: ["docker/.env"])
    project_name: str = ""              # prefixo de volume (docker compose project)
    ephemeral_runner: EphemeralRunnerConfig | None = None
    clean: dict[str, list[str]] = field(default_factory=dict)  # categoria -> volumes


@dataclass
class RuntimeConfig:
    orchestrator: str = "docker_compose"
    infra: list[str] = field(default_factory=list)
    default_max_strikes: int = 3
    apps: dict[str, AppConfig] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)  # derivado de apps[*].aliases
    docker_compose: DockerComposeConfig | None = None


@dataclass
class WorkspaceConfig:
    name: str
    root: str
    demands_dir: str = "docs/RFC"
    platform: str = "azure_devops"
    auth_method: str = "token"
    jira_base_url: str = "https://atlassian.net/browse"
    jira_key_pattern: str = r"^[A-Z][A-Z0-9]+-\d+$"
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    platform_config: PlatformConfig = field(default_factory=PlatformConfig)
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    pr_doc_prefix: str = "99-pr-00"
    pr_doc_suffix_map: dict[str, str] = field(default_factory=dict)
    long_branches: list[str] = field(default_factory=lambda: ["master", "main", "desenv", "hml", "OG-GLOBAL"])
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_config_schema_v05.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/dop/config/schema.py tests/test_config_schema_v05.py
git commit -m "refactor(config): data-driven runtime schema (AppConfig/DockerComposeConfig)"
```

---

## Task 2: Loader parseia o novo schema (`config/loader.py`)

**Files:**
- Modify: `src/dop/config/loader.py`
- Test: `tests/test_config_loader_v05.py`

- [ ] **Step 1: Reescrever o teste do loader (falhará)**

Substituir TODO o conteúdo de `tests/test_config_loader_v05.py` por:

```python
import tomllib
import pytest
from dop.config.loader import _parse_workspace

TOML_WITH_RUNTIME = """
[workspaces.test]
root = "/tmp/test"

[workspaces.test.runtime]
orchestrator = "docker_compose"
infra = ["mongodb", "allure"]
default_max_strikes = 5

[workspaces.test.runtime.apps.optum-support-be]
service = "optum-support-be"
role = "backend"
port = 8080
debug_port = 5006
aliases = ["osb"]
url_env = "OPTUM_SUPPORT_BE_URL"
fallback_url_env = "AZURE_OPTUM_SUPPORT_BE_URL"

[workspaces.test.runtime.apps.optum-support-fe]
service = "optum-support-fe"
role = "frontend"
port = 5173
aliases = ["osf"]
depends_on = ["optum-support-be"]
e2e_suite = "optum-support-fe"
[workspaces.test.runtime.apps.optum-support-fe.build]
dir = "repos/optum-support-fe"
command = "npx vite build"
artifact = "dist"

[workspaces.test.runtime.docker_compose]
compose_file = "docker-compose.yml"
env_files = ["docker/.env", "docker/.env.runtime"]
project_name = "optum-dev"
[workspaces.test.runtime.docker_compose.ephemeral_runner]
service = "playwright-env"
profile = "e2e"
[workspaces.test.runtime.docker_compose.clean]
maven = ["m2-cache"]
node_modules = ["optum-fe-node_modules"]
"""


def test_parse_runtime_section():
    raw = tomllib.loads(TOML_WITH_RUNTIME)
    ws = _parse_workspace("test", raw["workspaces"]["test"])

    rt = ws.runtime
    assert rt.orchestrator == "docker_compose"
    assert rt.infra == ["mongodb", "allure"]
    assert rt.default_max_strikes == 5

    be = rt.apps["optum-support-be"]
    assert be.service == "optum-support-be"
    assert be.role == "backend"
    assert be.url_env == "OPTUM_SUPPORT_BE_URL"
    assert be.fallback_url_env == "AZURE_OPTUM_SUPPORT_BE_URL"

    fe = rt.apps["optum-support-fe"]
    assert fe.depends_on == ["optum-support-be"]
    assert fe.e2e_suite == "optum-support-fe"
    assert fe.build is not None
    assert fe.build.dir == "repos/optum-support-fe"
    assert fe.build.command == "npx vite build"

    assert rt.aliases == {"osb": "optum-support-be", "osf": "optum-support-fe"}

    dc = rt.docker_compose
    assert dc is not None
    assert dc.env_files == ["docker/.env", "docker/.env.runtime"]
    assert dc.project_name == "optum-dev"
    assert dc.ephemeral_runner.service == "playwright-env"
    assert dc.ephemeral_runner.profile == "e2e"
    assert dc.clean["maven"] == ["m2-cache"]


def test_parse_without_runtime_section():
    raw = tomllib.loads('[workspaces.test]\nroot = "/tmp/test"')
    ws = _parse_workspace("test", raw["workspaces"]["test"])
    assert ws.runtime.default_max_strikes == 3
    assert ws.runtime.apps == {}
    assert ws.runtime.docker_compose is None


def test_app_missing_required_field_raises():
    bad = """
[workspaces.test]
root = "/tmp/test"
[workspaces.test.runtime.apps.foo]
role = "backend"
port = 8080
"""
    raw = tomllib.loads(bad)
    with pytest.raises(KeyError):
        _parse_workspace("test", raw["workspaces"]["test"])
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_config_loader_v05.py -v`
Expected: FAIL (AttributeError/KeyError — loader ainda usa schema antigo).

- [ ] **Step 3: Atualizar `src/dop/config/loader.py`**

Trocar a linha de import (linha 7) por:

```python
from .schema import (
    WorkspaceConfig, RepoConfig, CredentialsConfig, PlatformConfig,
    RuntimeConfig, AppConfig, AppBuildConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
```

Substituir o bloco de parsing do runtime (linhas 64-96, do `rt_raw = data.get("runtime", {})` até o fim da construção de `runtime = RuntimeConfig(...)`) por:

```python
    rt_raw = data.get("runtime", {})
    apps_raw = rt_raw.get("apps", {})
    apps: dict[str, AppConfig] = {}
    aliases: dict[str, str] = {}
    for app_name, app_data in apps_raw.items():
        build_raw = app_data.get("build")
        build = None
        if build_raw is not None:
            build = AppBuildConfig(
                dir=build_raw["dir"],
                command=build_raw["command"],
                artifact=build_raw.get("artifact", "dist"),
            )
        app = AppConfig(
            name=app_name,
            service=app_data["service"],
            role=app_data["role"],
            port=app_data["port"],
            aliases=app_data.get("aliases", []),
            depends_on=app_data.get("depends_on", []),
            url_env=app_data.get("url_env"),
            fallback_url_env=app_data.get("fallback_url_env"),
            e2e_suite=app_data.get("e2e_suite"),
            build=build,
            debug_port=app_data.get("debug_port"),
        )
        apps[app_name] = app
        for alias in app.aliases:
            aliases[alias] = app_name

    dc_raw = rt_raw.get("docker_compose")
    docker_compose = None
    if dc_raw is not None:
        runner_raw = dc_raw.get("ephemeral_runner")
        ephemeral_runner = None
        if runner_raw is not None:
            ephemeral_runner = EphemeralRunnerConfig(
                service=runner_raw["service"],
                profile=runner_raw.get("profile"),
            )
        docker_compose = DockerComposeConfig(
            compose_file=dc_raw.get("compose_file", "docker-compose.yml"),
            env_files=dc_raw.get("env_files", ["docker/.env"]),
            project_name=dc_raw.get("project_name", ""),
            ephemeral_runner=ephemeral_runner,
            clean=dc_raw.get("clean", {}),
        )

    runtime = RuntimeConfig(
        orchestrator=rt_raw.get("orchestrator", "docker_compose"),
        infra=rt_raw.get("infra", []),
        default_max_strikes=rt_raw.get("default_max_strikes", 3),
        apps=apps,
        aliases=aliases,
        docker_compose=docker_compose,
    )
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_config_loader_v05.py tests/test_config_loader.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/config/loader.py tests/test_config_loader_v05.py
git commit -m "refactor(config): parse data-driven runtime + docker_compose sections"
```

---

## Task 3: `resolve.py` data-driven (sem `_BE_URL_MAP`)

**Files:**
- Modify: `src/dop/runtime/resolve.py`
- Test: `tests/test_runtime_resolve.py`

- [ ] **Step 1: Reescrever o teste (falhará)**

Substituir TODO o conteúdo de `tests/test_runtime_resolve.py` por:

```python
import pytest
from dop.config.schema import WorkspaceConfig, RuntimeConfig, AppConfig
from dop.runtime.resolve import expand_apps, infer_urls


def _ws() -> WorkspaceConfig:
    apps = {
        "lifesupport-api": AppConfig(
            name="lifesupport-api", service="lifesupport-api", role="backend",
            port=8082, debug_port=5005, aliases=["ls"],
            url_env="LIFESUPPORT_URL", fallback_url_env="AZURE_LIFESUPPORT_URL",
        ),
        "optum-support-be": AppConfig(
            name="optum-support-be", service="optum-support-be", role="backend",
            port=8080, debug_port=5006, aliases=["osb"],
            url_env="OPTUM_SUPPORT_BE_URL", fallback_url_env="AZURE_OPTUM_SUPPORT_BE_URL",
        ),
        "optum-support-fe": AppConfig(
            name="optum-support-fe", service="optum-support-fe", role="frontend",
            port=5173, aliases=["osf"], depends_on=["optum-support-be"],
            e2e_suite="optum-support-fe",
        ),
    }
    aliases = {"ls": "lifesupport-api", "osb": "optum-support-be", "osf": "optum-support-fe"}
    runtime = RuntimeConfig(apps=apps, aliases=aliases)
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=runtime)


def test_expand_aliases():
    assert expand_apps(_ws(), ["osf", "osb"]) == {"optum-support-fe", "optum-support-be"}


def test_expand_auto_deps():
    result = expand_apps(_ws(), ["osf"])
    assert result == {"optum-support-fe", "optum-support-be"}


def test_expand_no_deps():
    assert expand_apps(_ws(), ["osf"], no_deps=True) == {"optum-support-fe"}


def test_infer_urls_local(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    env = infer_urls(_ws(), {"lifesupport-api", "optum-support-be"})
    assert env["LIFESUPPORT_URL"] == "http://lifesupport-api:8082"
    assert env["OPTUM_SUPPORT_BE_URL"] == "http://optum-support-be:8080"


def test_infer_urls_fallback(monkeypatch):
    monkeypatch.setenv("AZURE_LIFESUPPORT_URL", "https://azure.example.com")
    env = infer_urls(_ws(), {"optum-support-be"})
    assert env["LIFESUPPORT_URL"] == "https://azure.example.com"


def test_expand_unknown_app_raises():
    with pytest.raises(Exception):
        expand_apps(_ws(), ["unknown-app"])
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_runtime_resolve.py -v`
Expected: FAIL (infer_urls usa `_BE_URL_MAP`; AppConfig sem `repo`/`kind`).

- [ ] **Step 3: Reescrever `src/dop/runtime/resolve.py`**

Substituir TODO o conteúdo por:

```python
from __future__ import annotations

import os

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError


def expand_apps(
    ws: WorkspaceConfig,
    tokens: list[str],
    *,
    no_deps: bool = False,
) -> set[str]:
    """Resolve alias tokens to canonical app names and auto-add `depends_on`.

    Args:
        ws: Workspace configuration with runtime apps and aliases.
        tokens: App names or aliases provided by the user.
        no_deps: When True, skip automatic dependency injection.

    Returns:
        Set of canonical app names.

    Raises:
        ValidationError: If any token does not resolve to a known app.
    """
    aliases = ws.runtime.aliases
    apps = ws.runtime.apps

    resolved: set[str] = set()
    for token in tokens:
        name = aliases.get(token, token)
        if name not in apps:
            available_apps = ", ".join(sorted(apps))
            available_aliases = ", ".join(sorted(aliases))
            raise ValidationError(
                f"App '{token}' not found. "
                f"Available apps: {available_apps} "
                f"(aliases: {available_aliases})"
            )
        resolved.add(name)

    if not no_deps:
        # Fixpoint: add transitive depends_on of every resolved app.
        changed = True
        while changed:
            changed = False
            for name in list(resolved):
                for dep in apps[name].depends_on:
                    if dep in apps and dep not in resolved:
                        resolved.add(dep)
                        changed = True

    return resolved


def infer_urls(
    ws: WorkspaceConfig,
    requested: set[str],
) -> dict[str, str]:
    """Infer backend URL environment variables for the requested app set.

    For every backend app that declares ``url_env``:
      - if it is in *requested*, inject the local service URL
        ``http://<service>:<port>``;
      - otherwise inject the value of ``fallback_url_env`` from the
        environment, when present.

    Returns:
        Dict of env var name -> URL string.
    """
    env: dict[str, str] = {}
    for app in ws.runtime.apps.values():
        if app.role != "backend" or not app.url_env:
            continue
        if app.name in requested:
            env[app.url_env] = f"http://{app.service}:{app.port}"
        elif app.fallback_url_env:
            fallback = os.environ.get(app.fallback_url_env, "")
            if fallback:
                env[app.url_env] = fallback
    return env
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_runtime_resolve.py -v`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/resolve.py tests/test_runtime_resolve.py
git commit -m "refactor(runtime): config-driven expand_apps/infer_urls"
```

---

## Task 4: `e2e.py` ordem de suites data-driven (sem `E2E_SUITE_ORDER`)

**Files:**
- Modify: `src/dop/runtime/e2e.py`
- Test: `tests/test_runtime_e2e.py`

- [ ] **Step 1: Reescrever o teste (falhará)**

Substituir TODO o conteúdo de `tests/test_runtime_e2e.py` por:

```python
from dop.config.schema import WorkspaceConfig, RuntimeConfig, AppConfig
from dop.runtime.e2e import (
    resolve_e2e_target, normalize_jira_filter, find_suites_for_jira,
    next_run_number, suite_order,
)


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173, e2e_suite="optum-support-fe"),
        "providers-front-end": AppConfig(name="providers-front-end", service="providers-front-end",
                                         role="frontend", port=5174, e2e_suite="providers-front-end"),
    }
    return WorkspaceConfig(name="test", root="/tmp/test", runtime=RuntimeConfig(apps=apps))


def test_suite_order_from_config():
    assert suite_order(_ws()) == ["optum-support-fe", "providers-front-end"]


def test_normalize_jira_filter():
    assert normalize_jira_filter("OG-150") == "og_150"
    assert normalize_jira_filter("SUOPT-3144") == "suopt_3144"


def test_resolve_target_suite():
    result = resolve_e2e_target("optum-support-fe", known_suites=["optum-support-fe"])
    assert result == {"kind": "suite", "suites": ["optum-support-fe"], "filter": None}


def test_resolve_target_jira():
    result = resolve_e2e_target("OG-150", known_suites=["optum-support-fe"])
    assert result["kind"] == "jira"
    assert result["filter"] == "og_150"


def test_resolve_target_file():
    result = resolve_e2e_target("tests/test_smoke.py", known_suites=["optum-support-fe"])
    assert result["kind"] == "file"


def test_find_suites_for_jira(tmp_path):
    suite1 = tmp_path / "optum-support-fe" / "tests"
    suite1.mkdir(parents=True)
    (suite1 / "test_og_150_login.py").touch()
    suite2 = tmp_path / "providers-front-end" / "tests"
    suite2.mkdir(parents=True)
    (suite2 / "test_smoke.py").touch()

    found = find_suites_for_jira("og_150", e2e_root=tmp_path,
                                 suite_order=["optum-support-fe", "providers-front-end"])
    assert found == ["optum-support-fe"]


def test_next_run_number(tmp_path):
    reports = tmp_path / "OG-150" / "optum-support-fe"
    reports.mkdir(parents=True)
    (reports / "run-1").mkdir()
    (reports / "run-2").mkdir()
    assert next_run_number(reports) == 3


def test_next_run_number_empty(tmp_path):
    reports = tmp_path / "OG-150" / "optum-support-fe"
    reports.mkdir(parents=True)
    assert next_run_number(reports) == 1
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_runtime_e2e.py -v`
Expected: FAIL (ImportError: cannot import name 'suite_order').

- [ ] **Step 3: Reescrever `src/dop/runtime/e2e.py`**

Substituir TODO o conteúdo por:

```python
from __future__ import annotations
import re
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError

JIRA_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def suite_order(ws: WorkspaceConfig) -> list[str]:
    """Return e2e suite names in config order (frontend apps with `e2e_suite`)."""
    return [
        app.e2e_suite
        for app in ws.runtime.apps.values()
        if app.role == "frontend" and app.e2e_suite
    ]


def normalize_jira_filter(key: str) -> str:
    """Normalize a JIRA key to the file-name fragment used in e2e test files.

    Examples:
        OG-150     -> og_150
        SUOPT-3144 -> suopt_3144
    """
    return key.lower().replace("-", "_")


def resolve_e2e_target(token: str, *, known_suites: list[str]) -> dict:
    """Resolve a ``dop e2e <target>`` token into a structured descriptor.

    Returns a dict with ``"kind"`` in {"suite", "jira", "file"}.
    Raises :class:`ValidationError` when the token cannot be resolved.
    """
    if token in known_suites:
        return {"kind": "suite", "suites": [token], "filter": None}

    if JIRA_RE.match(token):
        return {"kind": "jira", "suites": None, "filter": normalize_jira_filter(token)}

    if "/" in token or token.endswith(".py"):
        return {"kind": "file", "suites": None, "filter": None, "path": token}

    raise ValidationError(
        f"Cannot resolve e2e target '{token}'. "
        f"Expected: suite name ({', '.join(known_suites)}), JIRA key (OG-123), or file path."
    )


def find_suites_for_jira(
    jira_filter: str,
    *,
    e2e_root: Path,
    suite_order: list[str],
) -> list[str]:
    """Return suites (in *suite_order*) that contain test files matching *jira_filter*.

    A test file matches when ``jira_filter`` appears anywhere in its filename.
    """
    matched: list[str] = []
    for suite in suite_order:
        tests_dir = e2e_root / suite / "tests"
        if not tests_dir.is_dir():
            continue
        for f in tests_dir.iterdir():
            if f.is_file() and jira_filter in f.name:
                matched.append(suite)
                break
    return matched


def next_run_number(report_dir: Path) -> int:
    """Return the next sequential run number for Allure reports."""
    if not report_dir.is_dir():
        return 1
    existing = [
        int(d.name.split("-")[1])
        for d in report_dir.iterdir()
        if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()
    ]
    return max(existing, default=0) + 1
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_runtime_e2e.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/e2e.py tests/test_runtime_e2e.py
git commit -m "refactor(runtime): derive e2e suite order from config"
```

---

## Task 5: ABC do orquestrador (`runtime/orchestrator/base.py`)

**Files:**
- Create: `src/dop/runtime/orchestrator/__init__.py` (vazio por ora — preenchido na Task 7)
- Create: `src/dop/runtime/orchestrator/base.py`
- Test: `tests/test_orchestrator_base.py`

- [ ] **Step 1: Escrever o teste (falhará)**

Criar `tests/test_orchestrator_base.py`:

```python
import inspect
from dop.runtime.orchestrator.base import RuntimeProvider, ServiceStatus


def test_service_status_fields():
    s = ServiceStatus(name="optum-support-be", service="optum-support-be",
                      state="running", health="healthy", port=8080, up=True)
    assert s.name == "optum-support-be"
    assert s.up is True


def test_runtime_provider_is_abstract():
    with __import__("pytest").raises(TypeError):
        RuntimeProvider()  # ABC sem implementação


def test_runtime_provider_interface():
    for method in ("up", "stop", "restart", "status", "logs", "run_ephemeral", "clean"):
        assert hasattr(RuntimeProvider, method)
        assert inspect.isfunction(getattr(RuntimeProvider, method))
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_orchestrator_base.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Criar os módulos**

Criar `src/dop/runtime/orchestrator/__init__.py` vazio (uma linha em branco).

Criar `src/dop/runtime/orchestrator/base.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ServiceStatus:
    name: str               # nome lógico do app (ou serviço de infra)
    service: str            # nome do serviço no backend
    state: str | None       # running | exited | ...
    health: str | None      # healthy | starting | ...
    port: int | None
    up: bool


class RuntimeProvider(ABC):
    """Abstração de orquestrador de runtime (docker_compose, k8s, ...).

    Opera sobre nomes LÓGICOS de app; a implementação mapeia para serviços do
    backend. `infra` é sempre incluída em `up`.
    """

    @abstractmethod
    def up(self, apps: list[str], *, build: bool = False, wait: bool = True,
           dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def stop(self, apps: list[str], *, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def restart(self, apps: list[str], *, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def status(self, *, dry_run: bool = False, logger=None) -> list[ServiceStatus]: ...

    @abstractmethod
    def logs(self, apps: list[str], *, follow: bool = True, tail: int | None = None,
             since: str | None = None, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def run_ephemeral(self, args: list[str], *, env: dict[str, str],
                      dry_run: bool = False, logger=None,
                      exec_replace: bool = False) -> int: ...

    @abstractmethod
    def clean(self, categories: list[str], *, dry_run: bool = False, logger=None) -> list[str]: ...
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_orchestrator_base.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/orchestrator/__init__.py src/dop/runtime/orchestrator/base.py tests/test_orchestrator_base.py
git commit -m "feat(runtime): add RuntimeProvider ABC + ServiceStatus"
```

---

## Task 6: Provider docker_compose (`runtime/orchestrator/docker_compose.py`)

**Files:**
- Create: `src/dop/runtime/orchestrator/docker_compose.py`
- Test: `tests/test_orchestrator_docker_compose.py`

- [ ] **Step 1: Escrever o teste (falhará)**

Criar `tests/test_orchestrator_docker_compose.py`:

```python
from unittest.mock import patch
import pytest
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime.orchestrator.docker_compose import DockerComposeProvider
from dop.core.errors import ValidationError


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173),
    }
    dc = DockerComposeConfig(
        compose_file="docker-compose.yml",
        env_files=["docker/.env", "docker/.env.runtime"],
        project_name="optum-dev",
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
        clean={"maven": ["m2-cache"], "node_modules": ["optum-fe-node_modules"]},
    )
    rt = RuntimeConfig(apps=apps, infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="test", root="/tmp/ws", runtime=rt)


def test_missing_docker_compose_config_raises():
    rt = RuntimeConfig(apps={}, docker_compose=None)
    ws = WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)
    with pytest.raises(ValidationError):
        DockerComposeProvider(ws)


def test_up_includes_infra_and_maps_services():
    p = DockerComposeProvider(_ws())
    with patch("dop.runtime.orchestrator.docker_compose.run_command") as rc:
        p.up(["optum-support-fe"], dry_run=True)
    cmd = rc.call_args.args[0]
    assert "up" in cmd and "-d" in cmd and "--wait" in cmd
    assert "optum-support-fe" in cmd
    assert "mongodb" in cmd and "allure" in cmd


def test_status_parses_ps_json():
    p = DockerComposeProvider(_ws())
    fake = '{"Service": "optum-support-be", "State": "running", "Health": "healthy"}\n'
    with patch("dop.runtime.orchestrator.docker_compose.subprocess.run") as sp:
        sp.return_value.stdout = fake
        statuses = p.status()
    be = next(s for s in statuses if s.name == "optum-support-be")
    assert be.up is True
    assert be.state == "running"
    fe = next(s for s in statuses if s.name == "optum-support-fe")
    assert fe.up is False


def test_run_ephemeral_returns_zero_on_success():
    p = DockerComposeProvider(_ws())
    with patch("dop.runtime.orchestrator.docker_compose.run_command") as rc:
        code = p.run_ephemeral(["/e2e/optum-support-fe", "-k", "smoke"],
                               env={"E2E_BASE_URL": "http://localhost:5173"}, dry_run=True)
    assert code == 0
    cmd = rc.call_args.args[0]
    assert "playwright-env" in cmd
    assert "--profile" in cmd and "e2e" in cmd


def test_run_ephemeral_returns_one_on_process_error():
    from dop.core.errors import ProcessError
    p = DockerComposeProvider(_ws())
    with patch("dop.runtime.orchestrator.docker_compose.run_command", side_effect=ProcessError("boom")):
        code = p.run_ephemeral(["/e2e/x"], env={})
    assert code == 1


def test_clean_resolves_volumes_with_prefix():
    p = DockerComposeProvider(_ws())
    with patch("dop.runtime.orchestrator.docker_compose.subprocess.run") as sp:
        removed = p.clean(["maven", "node_modules"], dry_run=True)
    assert removed == ["m2-cache", "optum-fe-node_modules"]
    sp.assert_not_called()  # dry_run


def test_clean_all_expands_all_categories():
    p = DockerComposeProvider(_ws())
    removed = p.clean(["all"], dry_run=True)
    assert set(removed) == {"m2-cache", "optum-fe-node_modules"}
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_orchestrator_docker_compose.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Criar `src/dop/runtime/orchestrator/docker_compose.py`**

```python
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ...config.schema import WorkspaceConfig
from ...core.errors import ProcessError, ValidationError
from ...core.process import run_command
from ..compose import (
    build_up_command, build_stop_command, build_logs_command,
    build_run_command, build_ps_command,
)
from .base import RuntimeProvider, ServiceStatus


class DockerComposeProvider(RuntimeProvider):
    def __init__(self, ws: WorkspaceConfig):
        self.ws = ws
        self.cfg = ws.runtime.docker_compose
        if self.cfg is None:
            raise ValidationError(
                "Missing [runtime.docker_compose] config for the 'docker_compose' orchestrator."
            )

    # --- helpers ---
    def _root(self) -> Path:
        return Path(self.ws.root)

    def _compose_file(self) -> Path:
        return self._root() / self.cfg.compose_file

    def _env_files(self) -> list[Path]:
        return [self._root() / e for e in self.cfg.env_files]

    def _service(self, app_name: str) -> str:
        app = self.ws.runtime.apps.get(app_name)
        return app.service if app else app_name

    # --- lifecycle ---
    def up(self, apps, *, build=False, wait=True, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps}) + list(self.ws.runtime.infra)
        cmd = build_up_command(
            compose_file=self._compose_file(),
            env_files=self._env_files(),
            services=services, wait=wait, build=build,
        )
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def stop(self, apps, *, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = build_stop_command(compose_file=self._compose_file(), services=services)
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def restart(self, apps, *, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = ["docker", "compose", "-f", str(self._compose_file()), "restart"] + services
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def status(self, *, dry_run=False, logger=None) -> list[ServiceStatus]:
        cmd = build_ps_command(compose_file=self._compose_file())
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {' '.join(cmd)}")
            return []
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self._root()))
        containers: list[dict] = []
        for line in (result.stdout or "").strip().splitlines():
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        def _match(service: str):
            return next(
                (c for c in containers if c.get("Name") == service or c.get("Service") == service),
                None,
            )

        statuses: list[ServiceStatus] = []
        for app_name, app in self.ws.runtime.apps.items():
            m = _match(app.service)
            statuses.append(ServiceStatus(
                name=app_name, service=app.service,
                state=(m or {}).get("State"), health=((m or {}).get("Health") or None),
                port=app.port, up=m is not None,
            ))
        for infra in self.ws.runtime.infra:
            m = _match(infra)
            statuses.append(ServiceStatus(
                name=infra, service=infra,
                state=(m or {}).get("State"), health=((m or {}).get("Health") or None),
                port=None, up=m is not None,
            ))
        return statuses

    def logs(self, apps, *, follow=True, tail=None, since=None, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = build_logs_command(
            compose_file=self._compose_file(), services=services,
            follow=follow, tail=tail, since=since,
        )
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {' '.join(cmd)}")
            return
        os.execvp(cmd[0], cmd)  # pragma: no cover

    def run_ephemeral(self, args, *, env, dry_run=False, logger=None, exec_replace=False) -> int:
        runner = self.cfg.ephemeral_runner
        if runner is None:
            raise ValidationError(
                "No ephemeral_runner configured in [runtime.docker_compose.ephemeral_runner]."
            )
        cmd = build_run_command(
            compose_file=self._compose_file(),
            env_files=None if exec_replace else self._env_files(),
            service=runner.service, args=args, profile=runner.profile, extra_env=env,
        )
        if exec_replace:
            if dry_run:
                if logger:
                    logger.info(f"WOULD RUN: {' '.join(cmd)}")
                return 0
            os.execvp(cmd[0], cmd)  # pragma: no cover
            return 0  # pragma: no cover
        try:
            run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)
            return 0
        except ProcessError:
            return 1

    def clean(self, categories, *, dry_run=False, logger=None) -> list[str]:
        clean_map = self.cfg.clean
        cats = list(clean_map.keys()) if "all" in categories else categories
        volumes: list[str] = []
        for cat in cats:
            volumes += clean_map.get(cat, [])
        prefix = f"{self.cfg.project_name}_" if self.cfg.project_name else ""
        for vol in volumes:
            if not dry_run:
                subprocess.run(["docker", "volume", "rm", "-f", f"{prefix}{vol}"], capture_output=True)
            if logger:
                logger.info(f"Removed volume: {vol}")
        return volumes
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_orchestrator_docker_compose.py -v`
Expected: PASS (7 testes).

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/orchestrator/docker_compose.py tests/test_orchestrator_docker_compose.py
git commit -m "feat(runtime): DockerComposeProvider implementing RuntimeProvider"
```

---

## Task 7: Factory `build_runtime_provider` (`runtime/orchestrator/__init__.py`)

**Files:**
- Modify: `src/dop/runtime/orchestrator/__init__.py`
- Test: `tests/test_orchestrator_factory.py`

- [ ] **Step 1: Escrever o teste (falhará)**

Criar `tests/test_orchestrator_factory.py`:

```python
import pytest
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime.orchestrator import build_runtime_provider
from dop.runtime.orchestrator.docker_compose import DockerComposeProvider
from dop.core.errors import ValidationError


def _ws(orchestrator: str) -> WorkspaceConfig:
    dc = DockerComposeConfig(ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"))
    rt = RuntimeConfig(orchestrator=orchestrator, docker_compose=dc)
    return WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)


def test_builds_docker_compose():
    assert isinstance(build_runtime_provider(_ws("docker_compose")), DockerComposeProvider)


@pytest.mark.parametrize("orch", ["kubernetes", "okd", "rancher"])
def test_not_implemented_orchestrators(orch):
    with pytest.raises(ValidationError) as exc:
        build_runtime_provider(_ws(orch))
    assert "não implementado" in str(exc.value)


def test_unknown_orchestrator():
    with pytest.raises(ValidationError):
        build_runtime_provider(_ws("nope"))
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_orchestrator_factory.py -v`
Expected: FAIL (ImportError: cannot import name 'build_runtime_provider').

- [ ] **Step 3: Preencher `src/dop/runtime/orchestrator/__init__.py`**

```python
from __future__ import annotations

from ...config.schema import WorkspaceConfig
from ...core.errors import ValidationError
from .base import RuntimeProvider, ServiceStatus
from .docker_compose import DockerComposeProvider

_NOT_IMPLEMENTED = {"kubernetes", "okd", "rancher"}


def build_runtime_provider(ws: WorkspaceConfig) -> RuntimeProvider:
    """Select the runtime orchestrator implementation from `runtime.orchestrator`."""
    orchestrator = ws.runtime.orchestrator
    if orchestrator == "docker_compose":
        return DockerComposeProvider(ws)
    if orchestrator in _NOT_IMPLEMENTED:
        raise ValidationError(
            f"orchestrator '{orchestrator}' ainda não implementado; use 'docker_compose'."
        )
    raise ValidationError(f"orchestrator desconhecido: '{orchestrator}'.")


__all__ = ["build_runtime_provider", "RuntimeProvider", "ServiceStatus", "DockerComposeProvider"]
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_orchestrator_factory.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/orchestrator/__init__.py tests/test_orchestrator_factory.py
git commit -m "feat(runtime): build_runtime_provider factory (docker_compose; k8s/okd/rancher stubbed)"
```

---

## Task 8: Refatorar `handlers.py` para usar o provider + config

**Files:**
- Modify: `src/dop/runtime/handlers.py`
- Test: `tests/test_runtime_handlers.py` (novo)

- [ ] **Step 1: Escrever testes de handler (falhará)**

Criar `tests/test_runtime_handlers.py`:

```python
import argparse
from unittest.mock import MagicMock, patch
import pytest
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, AppConfig,
    DockerComposeConfig, EphemeralRunnerConfig,
)
from dop.runtime import handlers


def _ws() -> WorkspaceConfig:
    apps = {
        "optum-support-be": AppConfig(name="optum-support-be", service="optum-support-be",
                                      role="backend", port=8080,
                                      url_env="OPTUM_SUPPORT_BE_URL"),
        "optum-support-fe": AppConfig(name="optum-support-fe", service="optum-support-fe",
                                      role="frontend", port=5173, aliases=["osf"],
                                      depends_on=["optum-support-be"],
                                      e2e_suite="optum-support-fe"),
    }
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
        clean={"maven": ["m2-cache"]},
    )
    rt = RuntimeConfig(apps=apps, aliases={"osf": "optum-support-fe"},
                       infra=["mongodb", "allure"], docker_compose=dc)
    return WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)


def _args(**kw):
    return argparse.Namespace(**kw)


def test_handle_stop_delegates_to_provider():
    ws = _ws()
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_stop(ws, _args(apps=["osf"]), dry_run=True)
    assert rc == 0
    provider.stop.assert_called_once()


def test_handle_clean_maps_flags_to_categories():
    ws = _ws()
    provider = MagicMock()
    provider.clean.return_value = ["m2-cache"]
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        rc = handlers.handle_clean(ws, _args(m2=True, node_modules=False, allure=False, all=False), dry_run=True)
    assert rc == 0
    provider.clean.assert_called_once_with(["maven"], dry_run=True, logger=None)


def test_handle_clean_no_flags_raises():
    ws = _ws()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=MagicMock()):
        with pytest.raises(Exception):
            handlers.handle_clean(ws, _args(m2=False, node_modules=False, allure=False, all=False))


def test_suite_urls_from_config():
    ws = _ws()
    assert handlers._suite_base_url(ws, "optum-support-fe") == "http://localhost:5173"
    assert handlers._suite_api_url(ws, "optum-support-fe") == "http://localhost:8080"
```

- [ ] **Step 2: Rodar para ver falhar**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: FAIL (handlers ainda usa constantes/compose direto; `build_runtime_provider` não importado).

- [ ] **Step 3: Reescrever `src/dop/runtime/handlers.py`**

Substituir TODO o conteúdo por:

```python
# src/dop/runtime/handlers.py
from __future__ import annotations
import os
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError
from ..core.process import run_command
from .resolve import expand_apps, infer_urls
from .compose import write_env_runtime
from .orchestrator import build_runtime_provider


# --------------------------------------------------------------------------
# Path / env helpers
# --------------------------------------------------------------------------
def _ws_root(ws: WorkspaceConfig) -> Path:
    return Path(ws.root)


def _dc(ws: WorkspaceConfig):
    dc = ws.runtime.docker_compose
    if dc is None:
        raise ValidationError("Missing [runtime.docker_compose] config.")
    return dc


def _primary_env_file(ws: WorkspaceConfig) -> Path:
    return _ws_root(ws) / _dc(ws).env_files[0]


def _env_runtime_file(ws: WorkspaceConfig) -> Path:
    return _primary_env_file(ws).parent / ".env.runtime"


def _validate_env_file(ws: WorkspaceConfig) -> None:
    ef = _primary_env_file(ws)
    if not ef.exists():
        example = ef.parent / ".env.example"
        raise ValidationError(
            f"Environment file not found: {ef}\n"
            f"Copy {example} -> {ef} and fill in credentials."
        )


# --------------------------------------------------------------------------
# E2E suite URL helpers (derived from config)
# --------------------------------------------------------------------------
def _fe_for_suite(ws: WorkspaceConfig, suite: str):
    return next((a for a in ws.runtime.apps.values() if a.e2e_suite == suite), None)


def _suite_base_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    return f"http://localhost:{fe.port if fe else 5173}"


def _suite_api_url(ws: WorkspaceConfig, suite: str) -> str:
    fe = _fe_for_suite(ws, suite)
    if fe:
        for dep in fe.depends_on:
            be = ws.runtime.apps.get(dep)
            if be and be.role == "backend":
                return f"http://localhost:{be.port}"
    return "http://localhost:8080"


# --------------------------------------------------------------------------
# Front-end build (host)
# --------------------------------------------------------------------------
def _build_frontends(ws: WorkspaceConfig, requested: set[str], *, dry_run: bool = False, logger=None) -> None:
    for app_name in sorted(requested):
        app = ws.runtime.apps.get(app_name)
        if not app or app.build is None:
            continue
        app_path = _ws_root(ws) / app.build.dir
        artifact_path = app_path / app.build.artifact
        if not (app_path / "node_modules").is_dir():
            print(f"  ⚠ {app_name}: node_modules missing, running npm install first...")
            run_command(["npm", "install"], cwd=app_path, dry_run=dry_run, logger=logger)
        print(f"  Building {app_name}...")
        run_command(app.build.command.split(), cwd=app_path, dry_run=dry_run, logger=logger)
        if not dry_run and artifact_path.is_dir():
            print(f"  ✔ {app_name}: built → {artifact_path}")
        elif not dry_run:
            raise ValidationError(f"{app_name}: build succeeded but {app.build.artifact}/ not found at {artifact_path}")


def _check_port_available(port: int) -> None:
    import subprocess
    result = subprocess.run(["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        raise ValidationError(f"Port {port} already in use (PIDs: {result.stdout.strip()})")


# --------------------------------------------------------------------------
# Lifecycle handlers
# --------------------------------------------------------------------------
def handle_start(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    _validate_env_file(ws)
    requested = expand_apps(ws, args.apps, no_deps=getattr(args, "no_deps", False))

    for app_name in requested:
        app = ws.runtime.apps[app_name]
        _check_port_available(app.port)
        if app.debug_port:
            _check_port_available(app.debug_port)

    _build_frontends(ws, requested, dry_run=dry_run, logger=logger)

    env = infer_urls(ws, requested)
    write_env_runtime(_env_runtime_file(ws), env)

    if logger:
        logger.info(f"Starting: {', '.join(sorted(requested))}")
    provider.up(sorted(requested), wait=True, dry_run=dry_run, logger=logger)

    print(f"\nApps up: {', '.join(sorted(requested))}")
    for k, v in env.items():
        print(f"  {k} -> {v}")
    return 0


def handle_stop(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps: list[str] = []
    if getattr(args, "apps", None):
        apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.stop(apps, dry_run=dry_run, logger=logger)
    print(f"Stopped: {', '.join(apps) if apps else 'all'}")
    return 0


def handle_log(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    apps = sorted(expand_apps(ws, args.apps, no_deps=True))
    provider.logs(
        apps,
        follow=getattr(args, "follow", True),
        tail=getattr(args, "tail", None),
        since=getattr(args, "since", None),
        dry_run=dry_run, logger=logger,
    )
    return 0


def handle_rebuild(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    requested = expand_apps(ws, args.apps, no_deps=True)
    fe_apps = {a for a in requested if ws.runtime.apps[a].build is not None}
    if not fe_apps:
        raise ValidationError("No frontend apps to rebuild (apps without a [build] section).")
    _build_frontends(ws, fe_apps, dry_run=dry_run, logger=logger)
    provider.restart(sorted(fe_apps), dry_run=dry_run, logger=logger)
    print(f"✔ Rebuilt and restarted: {', '.join(sorted(fe_apps))}")
    return 0


def handle_status(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    statuses = provider.status(dry_run=dry_run, logger=logger)
    if dry_run:
        return 0

    app_names = set(ws.runtime.apps)
    print("Apps:")
    for s in statuses:
        if s.name not in app_names:
            continue
        if s.up:
            label = (s.state or "up") + (f" ({s.health})" if s.health else "")
            print(f"  ✔ {s.name:<25} {label:<15} :{s.port}")
        else:
            print(f"  ✗ {s.name:<25} down")

    print("\nInfra:")
    for s in statuses:
        if s.name in app_names:
            continue
        mark = "✔" if s.up else "✗"
        label = (s.state or ("up" if s.up else "down"))
        print(f"  {mark} {s.name:<25} {label}")
    return 0


def handle_restart(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    handle_stop(ws, args, dry_run=dry_run, logger=logger)
    handle_start(ws, args, dry_run=dry_run, logger=logger)
    return 0


# --------------------------------------------------------------------------
# E2E / codegen / report / clean
# --------------------------------------------------------------------------
def handle_e2e(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from .e2e import resolve_e2e_target, find_suites_for_jira, next_run_number, suite_order

    provider = build_runtime_provider(ws)
    e2e_root = _ws_root(ws) / "e2e"
    reports_root = e2e_root / "reports"
    order = suite_order(ws)
    known_suites = [
        d.name for d in e2e_root.iterdir()
        if d.is_dir() and (d / "tests").is_dir()
    ] if e2e_root.is_dir() else []

    targets = getattr(args, "targets", []) or []
    if not targets:
        raise ValidationError("No e2e target specified. Use: dop e2e <suite|JIRA|file>")

    jira_key = None
    suites_to_run: list[str] = []
    pytest_filter = getattr(args, "k", None)
    extra_pytest = list(getattr(args, "extra", []) or [])

    for token in targets:
        resolved = resolve_e2e_target(token, known_suites=known_suites)
        if resolved["kind"] == "suite":
            suites_to_run.extend(resolved["suites"])
        elif resolved["kind"] == "jira":
            jira_key = token.upper()
            jira_filter = resolved["filter"]
            found = find_suites_for_jira(jira_filter, e2e_root=e2e_root, suite_order=order)
            if not found:
                raise ValidationError(f"No tests found for {jira_key} in suites: {', '.join(order)}")
            suites_to_run.extend(found)
            if not pytest_filter:
                pytest_filter = jira_filter
        elif resolved["kind"] == "file":
            extra_pytest.append(resolved.get("path", token))

    # Deduplicate, preserve config suite order
    seen: set[str] = set()
    ordered: list[str] = []
    for s in order:
        if s in suites_to_run and s not in seen:
            ordered.append(s)
            seen.add(s)
    for s in suites_to_run:
        if s not in seen:
            ordered.append(s)
            seen.add(s)

    headed = getattr(args, "headed", False)
    all_green = True
    for suite in ordered:
        pytest_args: list[str] = []
        if pytest_filter:
            pytest_args += ["-k", pytest_filter]
        extra_env = {
            "E2E_BASE_URL": _suite_base_url(ws, suite),
            "E2E_API_URL": _suite_api_url(ws, suite),
        }
        if headed:
            pytest_args.append("--headed")
            extra_env["E2E_SHARED_CONTEXT"] = "1"
            extra_env["DISPLAY"] = os.environ.get("DISPLAY", ":0")
        pytest_args += extra_pytest

        report_jira = jira_key or "manual"
        report_dir = reports_root / report_jira / suite
        report_dir.mkdir(parents=True, exist_ok=True)
        run_n = next_run_number(report_dir)
        results_path = f"/e2e/reports/{report_jira}/{suite}/run-{run_n}/results"
        pytest_args += [f"--alluredir={results_path}"]

        if logger:
            logger.info(f"E2E suite: {suite} (run-{run_n})")
        code = provider.run_ephemeral(
            [f"/e2e/{suite}"] + pytest_args, env=extra_env, dry_run=dry_run, logger=logger,
        )
        if code == 0:
            print(f"  ✔ {suite}: green (run-{run_n})")
        else:
            print(f"  ✘ {suite}: red (run-{run_n})")
            all_green = False

    print(f"\nResult: {'green' if all_green else 'red'}")
    _generate_allure3_reports(e2e_root, suites=ordered, dry_run=dry_run)
    return 0 if all_green else 1


def _generate_allure3_reports(e2e_root: Path, *, suites: list, dry_run: bool = False) -> None:
    import shutil
    reports_root = e2e_root / "reports"
    for suite in suites:
        suite_dir = e2e_root / suite
        results_dir = suite_dir / ".allure-results"
        report_dir = reports_root / suite
        config_file = suite_dir / "allurerc.yml"
        if not results_dir.is_dir():
            continue
        if dry_run:
            print(f"  [dry-run] Would regenerate Allure report for {suite}")
            continue
        try:
            if report_dir.is_dir():
                shutil.rmtree(report_dir)
            cmd = ["allure", "generate", ".allure-results", "--output", str(report_dir), "--report-name", suite]
            if config_file.is_file():
                cmd += ["--config", "allurerc.yml"]
            run_command(cmd, cwd=suite_dir)
            print(f"  Allure: http://localhost:5252/{suite}/index.html")
        except Exception as e:
            print(f"  ⚠ Allure generate failed for {suite}: {e}")


def handle_codegen(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    from ..core.state import now_iso
    provider = build_runtime_provider(ws)
    suite = args.suite
    url = getattr(args, "url", None) or _suite_base_url(ws, suite)
    out = getattr(args, "out", None) or f"tests/recordings/recording_{now_iso()[:10]}.py"
    print(f"Starting codegen for suite '{suite}' → {url}")
    provider.run_ephemeral(
        ["playwright", "codegen", url, "-o", f"/e2e/{suite}/{out}"],
        env={"DISPLAY": os.environ.get("DISPLAY", ":0")},
        dry_run=dry_run, logger=logger, exec_replace=True,
    )
    return 0  # pragma: no cover


def handle_report(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    action = getattr(args, "report_action", None)
    if not action:
        raise ValidationError("Use: dop report serve|open|clean")

    if action == "serve":
        provider = build_runtime_provider(ws)
        provider.up([], wait=True, dry_run=dry_run, logger=logger)  # infra only (mongodb+allure)
        print("✔ Allure serving at http://localhost:5050")
        return 0

    if action == "open":
        import webbrowser
        suite = getattr(args, "suite", None) or ""
        jira = getattr(args, "jira", None) or ""
        url = f"http://localhost:5050/projects/{jira}/{suite}" if jira else "http://localhost:5050"
        webbrowser.open(url)
        return 0

    if action == "clean":
        import shutil
        keep = getattr(args, "keep", 5)
        reports_root = _ws_root(ws) / "e2e" / "reports"
        cleaned = 0
        if reports_root.is_dir():
            for jira_dir in reports_root.iterdir():
                if not jira_dir.is_dir() or jira_dir.name.startswith(("_", ".")):
                    continue
                for suite_dir in jira_dir.iterdir():
                    if not suite_dir.is_dir():
                        continue
                    runs = sorted(
                        [d for d in suite_dir.iterdir()
                         if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()],
                        key=lambda d: int(d.name.split("-")[1]),
                    )
                    for r in (runs[:-keep] if len(runs) > keep else []):
                        if not dry_run:
                            shutil.rmtree(r)
                        cleaned += 1
        print(f"✔ Cleaned {cleaned} old runs (keeping last {keep} per suite)")
        return 0
    return 1


def handle_clean(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    provider = build_runtime_provider(ws)
    categories: list[str] = []
    if getattr(args, "all", False):
        categories = ["all"]
    else:
        if getattr(args, "m2", False):
            categories.append("maven")
        if getattr(args, "node_modules", False):
            categories.append("node_modules")
        if getattr(args, "allure", False):
            categories.append("allure")
    if not categories:
        raise ValidationError("Specify --m2, --node-modules, --allure, or --all")
    removed = provider.clean(categories, dry_run=dry_run, logger=logger)
    print(f"✔ Cleaned {len(removed)} volumes")
    return 0
```

- [ ] **Step 4: Rodar para ver passar**

Run: `python -m pytest tests/test_runtime_handlers.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Rodar a suíte completa**

Run: `python -m pytest -q`
Expected: PASS (todos). Se algum teste antigo referenciar símbolos removidos, ajustar.

- [ ] **Step 6: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "refactor(runtime): handlers delegate to RuntimeProvider; remove hardcoded constants"
```

---

## Task 9: Config exemplo versionado + atualização da documentação

**Files:**
- Create: `docs/examples/config.optum.toml`
- Modify: `docs/reference/configuration.md`
- Modify: `docs/adr/0011-runtime-docker-compose.md` (status/nota)
- Create: `docs/adr/0014-abstracao-de-orquestrador-runtime.md`
- Modify: `docs/prd/0004-runtime-local.md` (refletir config-driven)

- [ ] **Step 1: Criar `docs/examples/config.optum.toml`**

Exemplo completo e válido do workspace Optum no novo schema (apps FE/BE com
`service`/`role`/`port`/`depends_on`/`url_env`/`fallback_url_env`/`e2e_suite`/`build`,
`[runtime]` com `orchestrator`/`infra`, e `[runtime.docker_compose]` com
`compose_file`/`env_files`/`project_name`/`ephemeral_runner`/`clean`). Conteúdo
derivado das constantes removidas (portas, volumes, build commands, runner).

- [ ] **Step 2: Atualizar `docs/reference/configuration.md`**

Substituir a seção de runtime pela nova estrutura (`[runtime]`,
`[runtime.apps.*]`, `[runtime.docker_compose]`), removendo `kind`/`dev_cmd`/
`fe_deps`/`compose_file`/`env_file`/`compose_timeout` e documentando os novos campos
e a tabela de dataclasses atualizada.

- [ ] **Step 3: Criar ADR-0014 e atualizar ADR-0011/PRD-0004**

ADR-0014 "Abstração de orquestrador de runtime (RuntimeProvider)": contexto,
decisão (provider pattern + factory por config; docker_compose agora), consequências.
Atualizar ADR-0011 com nota de que o runtime passou a ser data-driven (remoção das
dívidas) e PRD-0004 para refletir o config-driven.

- [ ] **Step 4: Commit**

```bash
git add docs/examples docs/reference/configuration.md docs/adr docs/prd
git commit -m "docs: document data-driven runtime + orchestrator abstraction; add example config"
```

- [ ] **Step 5: Atualizar o config real (`$DOP_CONFIG`) — manual/assistido**

Localizar o `config.toml` real (`echo $DOP_CONFIG` ou `~/.config/dop/config.toml`),
migrar a seção `[workspaces.optum.runtime]` para o novo schema usando
`docs/examples/config.optum.toml` como base. Validar com:
`python -c "from dop.config import load_config; load_config()"`.
(Fora do controle de versão deste repo; passo executado no ambiente do usuário.)

---

## Self-Review (preenchido)

- **Cobertura do spec:** §4 schema → Tasks 1-2; §5 provider → Tasks 5-7; §6 divisão
  de responsabilidades → Tasks 3-4 e 8; §7 erros → validações em Tasks 1/2/6/7/8;
  §8 testes → todos os passos TDD; §9 escopo (docs/exemplo) → Task 9. ✔
- **Placeholders:** nenhum — todo passo tem código/comando completo. ✔
- **Consistência de tipos:** `AppConfig`/`DockerComposeConfig`/`RuntimeProvider`/
  `ServiceStatus`/`build_runtime_provider`/`suite_order`/`run_ephemeral(...,
  exec_replace=...)`/`clean(categories)->list[str]` usados de forma idêntica entre
  schema, loader, resolve, e2e, provider e handlers. ✔
```
