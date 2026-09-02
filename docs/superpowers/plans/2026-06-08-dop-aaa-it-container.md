# `dop aaa` / `dop it` em container — Implementation Plan (v0.7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mudar `dop aaa`/`dop it` de Maven-no-host para Maven num container Java efêmero (`docker compose run --rm`), com runner configurável, reusando m2-cache, e Testcontainers via docker.sock + network_mode host. Inclui imagem, serviço compose, config e poms piloto mínimos no workspace Optum.

**Architecture:** Um runner Java dedicado (`java_runner` na config) é disparado por `provider.run_service(...)` com workdir `/workspace/<root_rel>/<repo>`; o container monta a raiz do workspace preservando o layout relativo dos poms piloto (parent = pom do app via relativePath). Allure inalterado.

**Tech Stack:** Python 3.13 (argparse/dataclasses/subprocess/unittest+pytest), Docker Compose, Maven (em container, eclipse-temurin:17), Testcontainers, Allure.

---

## Context for the implementer (read before starting)

- **dop-cli repo root:** `/opt/wks/dbo/dop/repos/dop-cli`. Branch já é `feat/dop-aaa-it-container`.
- **Rodar testes:** `python -m pytest <paths> -p no:playwright` (o `-p no:playwright` é OBRIGATÓRIO — senão o plugin pytest-playwright quebra na coleção). Há **uma** falha pré-existente não relacionada: `tests/test_handlers.py::TestPrPublish::test_commits_pushes_and_creates_prs` — ignore.
- **Estilo de testes:** `tests/test_runtime_handlers.py` usa funções `pytest` + `from unittest.mock import MagicMock, patch`, helpers `_ws()` e `_args(**kw)`, e `from dop.core.errors import ValidationError`. `tests/test_config_schema_v05.py` e `tests/test_runtime_allure.py` também existem.
- **`_ws()`** (em test_runtime_handlers.py) cria `WorkspaceConfig(root="/tmp/ws")` com `DockerComposeConfig(ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"), clean={...})`. Para testar o runner Java, os testes vão setar `ws.runtime.docker_compose.java_runner` explicitamente.
- **Estado atual (v0.6):** `_handle_maven_layer(ws, args, *, layer, root, maven_args, filter_prop, dry_run, logger)` roda host via `_run_maven`; `handle_aaa`/`handle_it` passam `root=_ws_root(ws)/ws.aaa_root|it_root`. Vamos trocar para container e mudar a assinatura para `root_rel: str`.
- **Workspace Optum:** `/opt/wks/csptech/optum` (NÃO é git). Já migrado: `test/e2e/`, `test_root="test/e2e"` no config. App poms: `com.optum:lifesupport:1.0.0-SNAPSHOT` e `com.optum:optum-support:0.0.1-SNAPSHOT`, ambos Spring Boot 2.5.7 / Java 17, ambos com `mvnw`.

---

## File Structure

**dop-cli (versionado, TDD):**
- `src/dop/config/schema.py` — `JavaRunnerConfig` + `DockerComposeConfig.java_runner`.
- `src/dop/config/loader.py` — parse de `java_runner`.
- `src/dop/runtime/compose.py` — `build_run_command(..., workdir=None)`.
- `src/dop/runtime/orchestrator/base.py` — `run_service` abstrato.
- `src/dop/runtime/orchestrator/docker_compose.py` — impl de `run_service`.
- `src/dop/runtime/handlers.py` — `_handle_maven_layer` via container; remove `_run_maven`.
- `src/dop/__init__.py` + `pyproject.toml` — bump 0.7.0.
- Tests: `test_config_schema_v05.py`, `test_runtime_compose.py` (novo), `test_runtime_orchestrator.py` (novo ou existente), `test_runtime_handlers.py`.

**Workspace Optum (integração, comandos reais):**
- `docker/java-test/Dockerfile` (novo).
- `docker-compose.yml` (+ serviço `java-test`).
- `~/.config/dop/config.toml` (+ `java_runner`).
- `test/aaa/lifesupport-api/{pom.xml,src/test/java/...}` (novo).
- `test/it/optum-support-be/{pom.xml,src/test/java/...}` (novo).

---

## Task 1: Config `java_runner`

**Files:**
- Modify: `src/dop/config/schema.py`
- Modify: `src/dop/config/loader.py`
- Test: `tests/test_config_schema_v05.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_config_schema_v05.py`:

```python
def test_java_runner_config_default_none():
    from dop.config.schema import DockerComposeConfig
    dc = DockerComposeConfig()
    assert dc.java_runner is None


def test_java_runner_config_values():
    from dop.config.schema import DockerComposeConfig, JavaRunnerConfig
    jr = JavaRunnerConfig(service="java-test", profile="test")
    dc = DockerComposeConfig(java_runner=jr)
    assert dc.java_runner.service == "java-test"
    assert dc.java_runner.profile == "test"
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_config_schema_v05.py::test_java_runner_config_values -p no:playwright -v`
Expected: FAIL (`ImportError`/`AttributeError` — `JavaRunnerConfig` / `java_runner` não existem).

- [ ] **Step 3: Add the dataclass + field** in `src/dop/config/schema.py`.

Add `JavaRunnerConfig` right after the existing `EphemeralRunnerConfig` dataclass:

```python
@dataclass
class JavaRunnerConfig:
    """Serviço usado para rodar Maven (aaa/it) em container efêmero."""
    service: str
    profile: str | None = None
```

Add the field to `DockerComposeConfig` (right after `ephemeral_runner`):

```python
    ephemeral_runner: EphemeralRunnerConfig | None = None
    java_runner: JavaRunnerConfig | None = None
```

- [ ] **Step 4: Parse it** in `src/dop/config/loader.py`. The `EphemeralRunnerConfig` import already exists; add `JavaRunnerConfig` to that import line from `.schema`. Then, inside the `if dc_raw is not None:` block, right after the `ephemeral_runner = ...` construction and before `docker_compose = DockerComposeConfig(...)`, add:

```python
        jr_raw = dc_raw.get("java_runner")
        java_runner = None
        if jr_raw is not None:
            java_runner = JavaRunnerConfig(
                service=jr_raw["service"],
                profile=jr_raw.get("profile"),
            )
```

And add `java_runner=java_runner,` to the `DockerComposeConfig(...)` call.

- [ ] **Step 5: Run, expect PASS**

Run: `python -m pytest tests/test_config_schema_v05.py -p no:playwright -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/dop/config/schema.py src/dop/config/loader.py tests/test_config_schema_v05.py
git commit -m "feat(config): add java_runner (container Maven runner) — ADR-16

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `build_run_command(workdir=...)`

**Files:**
- Modify: `src/dop/runtime/compose.py` (`build_run_command`)
- Test: `tests/test_runtime_compose.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/test_runtime_compose.py`:

```python
from pathlib import Path
from dop.runtime.compose import build_run_command


def test_build_run_command_with_workdir():
    cmd = build_run_command(
        compose_file=Path("/ws/docker-compose.yml"),
        service="java-test",
        args=["mvn", "test"],
        profile="test",
        workdir="/workspace/test/aaa/demo",
    )
    # -w must appear after `run --rm` and before the service name
    assert "run" in cmd and "--rm" in cmd
    assert "-w" in cmd
    assert cmd[cmd.index("-w") + 1] == "/workspace/test/aaa/demo"
    assert cmd.index("-w") < cmd.index("java-test")
    assert cmd.index("--rm") < cmd.index("-w")
    assert cmd[-2:] == ["mvn", "test"]


def test_build_run_command_without_workdir_unchanged():
    cmd = build_run_command(
        compose_file=Path("/ws/docker-compose.yml"),
        service="playwright-env",
        args=["/e2e/suite"],
        profile="e2e",
    )
    assert "-w" not in cmd
    assert cmd[-1] == "/e2e/suite"
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_runtime_compose.py::test_build_run_command_with_workdir -p no:playwright -v`
Expected: FAIL (`TypeError: unexpected keyword argument 'workdir'`).

- [ ] **Step 3: Implement** — in `src/dop/runtime/compose.py`, add `workdir` to the signature and emit `-w`:

Change the signature to add (after `extra_env`):
```python
    extra_env: dict[str, str] | None = None,
    workdir: str | None = None,
```

And in the body, after the `for k, v in (extra_env or {}).items(): cmd += ["-e", ...]` loop and before `cmd += [service] + args`, insert:
```python
    if workdir:
        cmd += ["-w", workdir]
```

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_runtime_compose.py -p no:playwright -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/compose.py tests/test_runtime_compose.py
git commit -m "feat(compose): build_run_command supports -w workdir

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `provider.run_service`

**Files:**
- Modify: `src/dop/runtime/orchestrator/base.py` (abstract method)
- Modify: `src/dop/runtime/orchestrator/docker_compose.py` (impl)
- Test: `tests/test_runtime_orchestrator.py` (create)

- [ ] **Step 1: Write the failing test** — create `tests/test_runtime_orchestrator.py`:

```python
from unittest.mock import MagicMock, patch
from dop.config.schema import (
    WorkspaceConfig, RuntimeConfig, DockerComposeConfig,
    EphemeralRunnerConfig, JavaRunnerConfig,
)
from dop.runtime.orchestrator.docker_compose import DockerComposeProvider


def _provider() -> DockerComposeProvider:
    dc = DockerComposeConfig(
        ephemeral_runner=EphemeralRunnerConfig(service="playwright-env", profile="e2e"),
        java_runner=JavaRunnerConfig(service="java-test", profile="test"),
    )
    rt = RuntimeConfig(docker_compose=dc)
    ws = WorkspaceConfig(name="t", root="/tmp/ws", runtime=rt)
    return DockerComposeProvider(ws)


def test_run_service_builds_and_returns_code():
    p = _provider()
    fake = MagicMock(); fake.returncode = 7
    with patch("dop.runtime.orchestrator.docker_compose.subprocess.run", return_value=fake) as sr:
        code = p.run_service("java-test", ["mvn", "test"], profile="test",
                             workdir="/workspace/test/aaa/demo")
    assert code == 7
    argv = sr.call_args.args[0]
    assert argv[:3] == ["docker", "compose", "-f"]
    assert "java-test" in argv and "-w" in argv
    assert argv[argv.index("-w") + 1] == "/workspace/test/aaa/demo"


def test_run_service_dry_run_skips_subprocess():
    p = _provider()
    with patch("dop.runtime.orchestrator.docker_compose.subprocess.run") as sr:
        code = p.run_service("java-test", ["mvn", "test"], dry_run=True)
    assert code == 0
    sr.assert_not_called()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_runtime_orchestrator.py::test_run_service_builds_and_returns_code -p no:playwright -v`
Expected: FAIL (`AttributeError: 'DockerComposeProvider' object has no attribute 'run_service'`).

- [ ] **Step 3a: Add abstract method to base** — in `src/dop/runtime/orchestrator/base.py`, inside `RuntimeProvider`, after the `run_ephemeral` abstractmethod, add:

```python
    @abstractmethod
    def run_service(self, service: str, args: list[str], *, profile: str | None = None,
                    workdir: str | None = None, env: dict[str, str] | None = None,
                    dry_run: bool = False, logger=None) -> int: ...
```

- [ ] **Step 3b: Implement in docker_compose** — in `src/dop/runtime/orchestrator/docker_compose.py`, add this method to `DockerComposeProvider` (e.g. right after `run_ephemeral`). It streams output (no capture) so Maven logs show live, and returns the container exit code:

```python
    def run_service(self, service, args, *, profile=None, workdir=None, env=None,
                    dry_run=False, logger=None) -> int:
        from ...core.security import guard_text
        cmd = build_run_command(
            compose_file=self._compose_file(),
            env_files=self._env_files(),
            service=service, args=args, profile=profile,
            extra_env=env, workdir=workdir,
        )
        display = " ".join(cmd)
        guard_text(display)
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {display}")
            return 0
        if logger:
            logger.info(f"RUN: {display}")
        result = subprocess.run(cmd, cwd=str(self._root()))
        return result.returncode
```

(`subprocess` and `build_run_command` are already imported at the top of `docker_compose.py`.)

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_runtime_orchestrator.py -p no:playwright -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dop/runtime/orchestrator/base.py src/dop/runtime/orchestrator/docker_compose.py tests/test_runtime_orchestrator.py
git commit -m "feat(orchestrator): add run_service (streamed compose run for Maven)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `_handle_maven_layer` via container (remove `_run_maven`)

**Files:**
- Modify: `src/dop/runtime/handlers.py`
- Test: `tests/test_runtime_handlers.py`

- [ ] **Step 1: Update tests** — in `tests/test_runtime_handlers.py`:

(a) DELETE the two host-runner tests `test_run_maven_returns_code` and `test_run_maven_dry_run` (the `_run_maven` helper is being removed).

(b) REPLACE the five aaa/it handler tests (`test_handle_aaa_runs_mvn_test_and_publishes`, `test_handle_aaa_uses_mvnw_when_present_and_k_filter`, `test_handle_it_runs_pit_verify`, `test_handle_aaa_red_when_mvn_fails`, `test_handle_aaa_all_multi_repo_mixed`, `test_handle_aaa_all_empty_root_is_noop_green`) with the container-based versions below. Keep the `_make_project` helper. Add a helper to build a ws with a java_runner + a mock provider:

```python
from dop.config.schema import JavaRunnerConfig


def _ws_with_java_runner(tmp_path):
    ws = _ws()
    ws.root = str(tmp_path)
    ws.test_root = "test/e2e"
    ws.aaa_root = "test/aaa"
    ws.it_root = "test/it"
    ws.runtime.docker_compose.java_runner = JavaRunnerConfig(service="java-test", profile="test")
    return ws


def test_handle_aaa_runs_in_container_and_publishes(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "lifesupport-api")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["lifesupport-api"], k=None, fresh_report=False))
    assert rc == 0
    kw = provider.run_service.call_args.kwargs
    args0 = provider.run_service.call_args.args
    assert args0[0] == "java-test"                       # service
    assert kw["workdir"] == "/workspace/test/aaa/lifesupport-api"
    assert kw["profile"] == "test"
    cmd = args0[1]
    assert cmd[0] == "mvn" and "test" in cmd
    assert pub.call_args.kwargs["project"] == "aaa-lifesupport-api"


def test_handle_aaa_uses_mvnw_when_present_and_k_filter(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "demo", mvnw=True)
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        handlers.handle_aaa(ws, _args(targets=["demo"], k="FooTest", fresh_report=False))
    cmd = provider.run_service.call_args.args[1]
    assert cmd[0] == "./mvnw"
    assert "-Dtest=FooTest" in cmd


def test_handle_it_runs_pit_verify_in_container(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/it", "optum-support-be")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_it(ws, _args(targets=["optum-support-be"], k=None, fresh_report=False))
    assert rc == 0
    kw = provider.run_service.call_args.kwargs
    cmd = provider.run_service.call_args.args[1]
    assert kw["workdir"] == "/workspace/test/it/optum-support-be"
    assert "-Pit" in cmd and "verify" in cmd
    assert pub.call_args.kwargs["project"] == "it-optum-support-be"


def test_handle_it_k_filter_uses_it_test(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/it", "demo")
    provider = MagicMock(); provider.run_service.return_value = 0
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        handlers.handle_it(ws, _args(targets=["demo"], k="FooIT", fresh_report=False))
    cmd = provider.run_service.call_args.args[1]
    assert "-Dit.test=FooIT" in cmd


def test_handle_aaa_red_when_container_fails(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "demo")
    provider = MagicMock(); provider.run_service.return_value = 1
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["demo"], k=None, fresh_report=False))
    assert rc == 1
    pub.assert_called_once()  # Allure published even on red


def test_handle_aaa_all_multi_repo_mixed(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    _make_project(tmp_path / "test/aaa", "a-pass")
    _make_project(tmp_path / "test/aaa", "z-fail")
    provider = MagicMock(); provider.run_service.side_effect = [0, 1]
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project") as pub:
        rc = handlers.handle_aaa(ws, _args(targets=["all"], k=None, fresh_report=False))
    assert rc == 1
    assert provider.run_service.call_count == 2
    assert pub.call_count == 2


def test_handle_aaa_all_empty_root_is_noop_green(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider), \
         patch("dop.runtime.handlers._publish_allure_project"):
        rc = handlers.handle_aaa(ws, _args(targets=["all"], k=None, fresh_report=False))
    assert rc == 0
    provider.run_service.assert_not_called()


def test_handle_aaa_without_java_runner_raises(tmp_path):
    ws = _ws_with_java_runner(tmp_path)
    ws.runtime.docker_compose.java_runner = None
    _make_project(tmp_path / "test/aaa", "demo")
    provider = MagicMock()
    with patch("dop.runtime.handlers.build_runtime_provider", return_value=provider):
        with pytest.raises(ValidationError):
            handlers.handle_aaa(ws, _args(targets=["demo"], k=None, fresh_report=False))
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/test_runtime_handlers.py::test_handle_aaa_runs_in_container_and_publishes -p no:playwright -v`
Expected: FAIL (handler still calls `_run_maven`, not `provider.run_service`).

- [ ] **Step 3: Rewrite the handlers** in `src/dop/runtime/handlers.py`.

(a) DELETE the `_run_maven` function entirely.

(b) Replace `_handle_maven_layer`, `handle_aaa`, `handle_it` with:

```python
def _handle_maven_layer(
    ws: WorkspaceConfig, args, *, layer: str, root_rel: str,
    maven_args: list[str], filter_prop: str, dry_run: bool = False, logger=None,
) -> int:
    """Shared driver for aaa/it: run Maven in the java_runner container, publish Allure."""
    root = _ws_root(ws) / root_rel
    repos = _resolve_test_targets(root, getattr(args, "targets", []) or [])
    if not repos:
        print(f"No {layer} projects found in {root}")
        return 0

    dc = _dc(ws)
    runner = dc.java_runner
    if runner is None:
        raise ValidationError(
            "No java_runner configured in "
            "[runtime.docker_compose.java_runner] (needed for dop aaa/it)."
        )

    provider = build_runtime_provider(ws)
    k = getattr(args, "k", None)
    fresh = getattr(args, "fresh_report", False)
    reports_root = _ws_root(ws) / ws.test_root / "reports"
    all_green = True

    for repo in repos:
        project_dir = root / repo
        mvn = "./mvnw" if (project_dir / "mvnw").is_file() else "mvn"
        cmd = [mvn] + list(maven_args)
        if k:
            cmd.append(f"{filter_prop}={k}")
        workdir = f"/workspace/{root_rel}/{repo}"
        if logger:
            logger.info(f"{layer}: {repo} (container {runner.service}, workdir {workdir})")
        code = provider.run_service(
            runner.service, cmd, profile=runner.profile, workdir=workdir,
            dry_run=dry_run, logger=logger,
        )
        if code == 0:
            print(f"  ✔ {repo}: green")
        else:
            print(f"  ✘ {repo}: red")
            all_green = False
        _publish_allure_project(
            project=f"{layer}-{repo}",
            results_dir=project_dir / ".allure-results",
            reports_root=reports_root,
            src=project_dir / "target" / "allure-results",
            fresh=fresh, dry_run=dry_run, logger=logger,
        )

    print(f"\nResult: {'green' if all_green else 'red'}")
    return 0 if all_green else 1


def handle_aaa(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    return _handle_maven_layer(
        ws, args, layer="aaa", root_rel=ws.aaa_root,
        maven_args=["test"], filter_prop="-Dtest", dry_run=dry_run, logger=logger,
    )


def handle_it(ws: WorkspaceConfig, args, *, dry_run: bool = False, logger=None) -> int:
    return _handle_maven_layer(
        ws, args, layer="it", root_rel=ws.it_root,
        maven_args=["-Pit", "verify"], filter_prop="-Dit.test", dry_run=dry_run, logger=logger,
    )
```

(`_dc(ws)` already exists in handlers.py and returns `ws.runtime.docker_compose` or raises. `build_runtime_provider` and `ValidationError` are already imported.)

- [ ] **Step 4: Run, expect PASS**

Run: `python -m pytest tests/test_runtime_handlers.py -p no:playwright -q`
Expected: all pass.

- [ ] **Step 5: Full dop-cli suite**

Run: `python -m pytest tests/ -p no:playwright -q`
Expected: only the known pre-existing `TestPrPublish` failure; everything else green.

- [ ] **Step 6: Commit**

```bash
git add src/dop/runtime/handlers.py tests/test_runtime_handlers.py
git commit -m "feat(test): run dop aaa/it in java_runner container (remove host path) — ADR-16

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Version bump 0.7.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/dop/__init__.py`

- [ ] **Step 1: Bump** — in `pyproject.toml` change `version = "0.6.0"` → `version = "0.7.0"`; in `src/dop/__init__.py` change `__version__ = "0.6.0"` → `__version__ = "0.7.0"`.

- [ ] **Step 2: Verify** — `python -m pytest tests/test_cli_parsing.py::TestCLIParsing::test_version_flag -p no:playwright -q` (the version test asserts the live `__version__`, so it stays green).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml src/dop/__init__.py
git commit -m "chore: bump version to 0.7.0 (dop aaa/it container runner)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Workspace — java-test image, compose service, config

**Files (workspace `/opt/wks/csptech/optum`, NOT git):**
- Create: `docker/java-test/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `~/.config/dop/config.toml`

- [ ] **Step 1: Create the image** — `/opt/wks/csptech/optum/docker/java-test/Dockerfile`:

```dockerfile
FROM eclipse-temurin:17-jdk-jammy
RUN apt-get update && apt-get install -y --no-install-recommends \
        maven netcat-openbsd ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
ENV MAVEN_OPTS="-Dmaven.repo.local=/root/.m2/repository"
```

- [ ] **Step 2: Add the compose service** — in `/opt/wks/csptech/optum/docker-compose.yml`, add under `services:` (e.g. right after `playwright-env`):

```yaml
  java-test:
    build:
      context: ./docker/java-test
      dockerfile: Dockerfile
    profiles: ["test"]
    network_mode: host
    working_dir: /workspace
    volumes:
      - .:/workspace:rw
      - m2-cache:/root/.m2/repository
      - /var/run/docker.sock:/var/run/docker.sock
    env_file:
      - docker/.env
    environment:
      MAVEN_OPTS: "-Dmaven.repo.local=/root/.m2/repository"
```

- [ ] **Step 3: Add config** — in `~/.config/dop/config.toml`, append under the optum docker_compose section (after the existing `[workspaces.optum.runtime.docker_compose.ephemeral_runner]` block):

```toml
[workspaces.optum.runtime.docker_compose.java_runner]
service = "java-test"
profile = "test"
```

- [ ] **Step 4: Build the image**

Run: `docker compose -f /opt/wks/csptech/optum/docker-compose.yml --project-directory /opt/wks/csptech/optum --profile test build java-test`
Expected: image builds; `mvn -v` works in it:
`docker compose -f /opt/wks/csptech/optum/docker-compose.yml --project-directory /opt/wks/csptech/optum --profile test run --rm java-test mvn -v`
Expected: prints Apache Maven 3.x + Java 17.

- [ ] **Step 5: Verify dop reads java_runner**

Run: `dop --workspace optum aaa all`
Expected: `No aaa projects found in /opt/wks/csptech/optum/test/aaa` (no ValidationError → java_runner is parsed). (Note: requires the dop-cli changes from Tasks 1-4 to be on the active editable install — confirm `git -C /opt/wks/dbo/dop/repos/dop-cli branch --show-current` is the feature branch.)

(No commit — workspace is not git.)

---

## Task 7: Workspace — pilot `test/aaa/lifesupport-api`

**Files (workspace, NOT git):**
- Create: `test/aaa/lifesupport-api/pom.xml`
- Create: `test/aaa/lifesupport-api/src/test/java/com/optum/aaa/SmokeAaaTest.java`

- [ ] **Step 1: Create the pom** — `/opt/wks/csptech/optum/test/aaa/lifesupport-api/pom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <parent>
    <groupId>com.optum</groupId>
    <artifactId>lifesupport</artifactId>
    <version>1.0.0-SNAPSHOT</version>
    <relativePath>../../../repos/lifesupport-api/pom.xml</relativePath>
  </parent>

  <artifactId>lifesupport-aaa-tests</artifactId>
  <name>lifesupport-aaa-tests</name>
  <packaging>jar</packaging>

  <properties>
    <allure.version>2.25.0</allure.version>
    <aspectj.version>1.9.21</aspectj.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.qameta.allure</groupId>
      <artifactId>allure-junit5</artifactId>
      <version>${allure.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.codehaus.mojo</groupId>
        <artifactId>build-helper-maven-plugin</artifactId>
        <version>3.5.0</version>
        <executions>
          <execution>
            <id>add-app-sources</id>
            <phase>generate-sources</phase>
            <goals><goal>add-source</goal></goals>
            <configuration>
              <sources>
                <source>../../../repos/lifesupport-api/src/main/java</source>
              </sources>
            </configuration>
          </execution>
        </executions>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
        <configuration>
          <argLine>
            -javaagent:"${settings.localRepository}/org/aspectj/aspectjweaver/${aspectj.version}/aspectjweaver-${aspectj.version}.jar"
          </argLine>
          <systemPropertyVariables>
            <allure.results.directory>${project.build.directory}/allure-results</allure.results.directory>
          </systemPropertyVariables>
        </configuration>
        <dependencies>
          <dependency>
            <groupId>org.aspectj</groupId>
            <artifactId>aspectjweaver</artifactId>
            <version>${aspectj.version}</version>
          </dependency>
        </dependencies>
      </plugin>
    </plugins>
  </build>
</project>
```

- [ ] **Step 2: Create the smoke test** — `/opt/wks/csptech/optum/test/aaa/lifesupport-api/src/test/java/com/optum/aaa/SmokeAaaTest.java`:

```java
package com.optum.aaa;

import io.qameta.allure.Feature;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@Feature("AAA smoke")
class SmokeAaaTest {

    @Test
    void should_compile_app_sources_and_run_a_pure_assertion() {
        // Arrange
        int a = 2, b = 3;
        // Act
        int sum = a + b;
        // Assert — the real value of this pilot is that build-helper added
        // ../../../repos/lifesupport-api/src/main/java as a source root, so
        // `mvn test` compiles the entire app against repos/<repo> before running.
        assertThat(sum).isEqualTo(5);
    }
}
```

- [ ] **Step 3: Run it via dop**

Run: `dop --workspace optum aaa lifesupport-api`
Expected: container runs `mvn test`; app sources compile; the smoke test passes; output ends with `Result: green` and `Allure: http://localhost:5252/aaa-lifesupport-api/index.html`.

- [ ] **Step 4: Verify in Allure UI**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5252/aaa-lifesupport-api/index.html`
Expected: `200`.

**If app compilation fails** (missing deps/source quirks): report it — it indicates the app itself doesn't compile cleanly under this setup, which is a finding for SUOPT-3184, not a dop bug. As a minimal fallback to prove the runner path, temporarily remove the `build-helper` plugin block (so only the smoke test compiles) and re-run; note this in the report.

---

## Task 8: Workspace — pilot `test/it/optum-support-be`

**Files (workspace, NOT git):**
- Create: `test/it/optum-support-be/pom.xml`
- Create: `test/it/optum-support-be/src/test/java/com/optum/it/SmokeContainerIT.java`

- [ ] **Step 1: Create the pom** — `/opt/wks/csptech/optum/test/it/optum-support-be/pom.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <parent>
    <groupId>com.optum</groupId>
    <artifactId>optum-support</artifactId>
    <version>0.0.1-SNAPSHOT</version>
    <relativePath>../../../repos/optum-support-be/pom.xml</relativePath>
  </parent>

  <artifactId>optum-support-it-tests</artifactId>
  <name>optum-support-it-tests</name>
  <packaging>jar</packaging>

  <properties>
    <allure.version>2.25.0</allure.version>
    <testcontainers.version>1.19.7</testcontainers.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-test</artifactId>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>org.testcontainers</groupId>
      <artifactId>testcontainers</artifactId>
      <version>${testcontainers.version}</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>org.testcontainers</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>${testcontainers.version}</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>org.testcontainers</groupId>
      <artifactId>mysql</artifactId>
      <version>${testcontainers.version}</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.qameta.allure</groupId>
      <artifactId>allure-junit5</artifactId>
      <version>${allure.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <profiles>
    <profile>
      <id>it</id>
      <build>
        <plugins>
          <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-failsafe-plugin</artifactId>
            <configuration>
              <systemPropertyVariables>
                <allure.results.directory>${project.build.directory}/allure-results</allure.results.directory>
              </systemPropertyVariables>
            </configuration>
            <executions>
              <execution>
                <goals>
                  <goal>integration-test</goal>
                  <goal>verify</goal>
                </goals>
              </execution>
            </executions>
          </plugin>
        </plugins>
      </build>
    </profile>
  </profiles>
</project>
```

- [ ] **Step 2: Create the IT smoke** — `/opt/wks/csptech/optum/test/it/optum-support-be/src/test/java/com/optum/it/SmokeContainerIT.java`:

```java
package com.optum.it;

import org.junit.jupiter.api.Test;
import org.testcontainers.containers.MySQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import static org.assertj.core.api.Assertions.assertThat;

@Testcontainers
class SmokeContainerIT {

    @Container
    static final MySQLContainer<?> MYSQL = new MySQLContainer<>("mysql:8.0");

    @Test
    void should_start_a_testcontainers_mysql_from_inside_the_runner() {
        // Arrange / Act — Testcontainers spawns a sibling container via the mounted
        // docker.sock; network_mode: host lets the test reach it on localhost.
        // Assert
        assertThat(MYSQL.isRunning()).isTrue();
        assertThat(MYSQL.getJdbcUrl()).startsWith("jdbc:mysql://");
    }
}
```

- [ ] **Step 3: Run it via dop**

Run: `dop --workspace optum it optum-support-be`
Expected: container runs `mvn -Pit verify`; Testcontainers pulls/starts `mysql:8.0`; the IT passes; output ends with `Result: green` and `Allure: http://localhost:5252/it-optum-support-be/index.html`.

**If Testcontainers fails on Ryuk** (reaper), add to the `java-test` service `environment:` in docker-compose.yml: `TESTCONTAINERS_RYUK_DISABLED: "true"`, rebuild not needed (env only), re-run, and note it in the report.

- [ ] **Step 4: Verify in Allure UI**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5252/it-optum-support-be/index.html`
Expected: `200`.

---

## Task 9: Finalize — merge dop-cli + update migration doc + memory

**Files:**
- Modify: `docs/workspace-migration-adr16.md` (dop-cli)
- Merge `feat/dop-aaa-it-container` → `main`

- [ ] **Step 1: Update the migration doc** — in `/opt/wks/dbo/dop/repos/dop-cli/docs/workspace-migration-adr16.md`, replace the "Maven no host" guidance and §5 prerequisites with the container model: add the `docker/java-test/Dockerfile`, the `java-test` compose service, and the `[workspaces.optum.runtime.docker_compose.java_runner]` config; change "Pré-requisitos do host: JDK17 + Maven" to "Pré-requisitos: Docker (a toolchain Java roda no container java-test; Testcontainers usa docker.sock + network_mode host)".

- [ ] **Step 2: Commit the doc**

```bash
cd /opt/wks/dbo/dop/repos/dop-cli
git add docs/workspace-migration-adr16.md
git commit -m "docs: container model for dop aaa/it (java-test runner) — ADR-16

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 3: Run full suite once more**

Run: `python -m pytest tests/ -p no:playwright -q`
Expected: only the known pre-existing `TestPrPublish` failure.

- [ ] **Step 4: Merge to main**

```bash
git checkout main
git merge --ff-only feat/dop-aaa-it-container
git branch -d feat/dop-aaa-it-container
dop --version   # expect: dop 0.7.0
```

- [ ] **Step 5: Update the Optum project memory** — append to the existing memory `/home/edbarros/.claude/projects/-opt-wks-csptech-optum/memory/adr-16-camadas-teste-aaa-it.md`: note that `dop aaa/it` now run Maven in the `java-test` container (not host), the `java_runner` config, the docker.sock+network_mode host Testcontainers pattern, and that pilot smoke poms exist at `test/aaa/lifesupport-api` and `test/it/optum-support-be`. Bump the dop version note to 0.7.0.

---

## Self-review notes (author)

- **Spec coverage:** config java_runner (T1) · build_run_command workdir (T2) · run_service (T3) · handlers container + remove _run_maven (T4) · version (T5) · image+service+config (T6) · pilot aaa (T7) · pilot it (T8) · finalize/merge/doc/memory (T9). All 4 acceptance criteria covered (AC1→T7, AC2→T8, AC3→T4 ValidationError test, AC4→untouched e2e path, verified by full suite in T4/T9).
- **No host path:** `_run_maven` removed (T4); host tests deleted; container-only.
- **Signature consistency:** `run_service(service, args, *, profile, workdir, env, dry_run, logger)` identical in base (T3), docker_compose (T3), and all call sites (T4). `_handle_maven_layer(..., root_rel, ...)` matches handle_aaa/handle_it callers (T4).
- **Container path mapping:** mount `.:/workspace`; workdir `/workspace/{root_rel}/{repo}`; parent relativePath `../../../repos/<repo>` resolves under `/workspace/repos/<repo>` (T6 mount + T7/T8 poms).
- **e2e untouched:** only `run_service` added; `run_ephemeral` and handle_e2e unchanged.
