"""The project's `dop.toml`: discovery, strict loading, validation and closure.

`dop` knows how to operate an environment and never which components exist:
everything project-specific is read from one `dop.toml` at the project root.
Every error here is a `ConfigError`, which the CLI turns into exit code 2.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.errors import ConfigError

CONFIG_FILENAME = "dop.toml"
CONFIG_ENV_VAR = "DOP_CONFIG"

KINDS = ("cluster", "process", "none")
TEST_LAYERS = ("unit", "integration", "contract")
DEFAULT_WAIT_TIMEOUT = "240s"

_PROJECT_KEYS = {"name", "state_dir", "default_environment"}
_ENVIRONMENT_KEYS = {
    "dir",
    "kubeconfig",
    "namespace",
    "cluster_exists",
    "cluster_running",
    "cluster_up",
    "cluster_start",
    "cluster_stop",
    "prepare",
    "up",
    "down",
    "status",
    "workloads",
    "wait_timeout",
    "addresses",
}
_COMPONENT_KEYS = {
    "repo",
    "kind",
    "needs",
    "setup",
    "run",
    "env",
    "ready",
    "address",
    "workloads",
    "logs",
    "test",
}
_LAYER_TABLE_KEYS = {"forwards", "commands"}
_TOP_KEYS = {"project", "environments", "components"}

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, None: 1.0}


@dataclass(frozen=True)
class Project:
    name: str
    state_dir: Path
    default_environment: str


@dataclass(frozen=True)
class Environment:
    dir: Path
    kubeconfig: str
    namespace: str
    cluster_exists: str | None = None
    cluster_running: str | None = None
    cluster_up: str | None = None
    cluster_start: str | None = None
    cluster_stop: str | None = None
    prepare: list[str] = field(default_factory=list)
    up: str | None = None
    down: str | None = None
    status: str | None = None
    workloads: list[str] = field(default_factory=list)
    wait_timeout: float = 240.0
    addresses: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TestLayer:
    forwards: list[str]
    commands: list[str]


@dataclass(frozen=True)
class Component:
    name: str
    repo: Path
    kind: str
    needs: list[str] = field(default_factory=list)
    setup: str | None = None
    run: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    ready: str | None = None
    address: str | None = None
    workloads: list[str] = field(default_factory=list)
    logs: str | None = None
    tests: dict[str, TestLayer] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    root: Path
    project: Project
    environments: dict[str, Environment]
    components: dict[str, Component]

    @property
    def environment(self) -> Environment:
        """The project's default environment."""
        return self.environments[self.project.default_environment]


# --------------------------------------------------------------------------- discovery


def find_config(cwd: Path | None = None) -> Path:
    """Return the `dop.toml` to use: `$DOP_CONFIG`, else the nearest one upwards."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise ConfigError(f"{CONFIG_ENV_VAR} points to {path}, which is not a file")
        return path.resolve()
    start = Path(cwd if cwd is not None else Path.cwd()).resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    raise ConfigError(f"no {CONFIG_FILENAME} found in {start} or any parent directory")


# --------------------------------------------------------------------------- helpers


def parse_duration(value: Any, where: str) -> float:
    """Parse "240s", "4m", "1h", "500ms" or a bare number of seconds."""
    if isinstance(value, bool):
        raise ConfigError(f"{where}: expected a duration like \"240s\", got {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and (match := _DURATION.match(value)):
        seconds = float(match.group(1)) * _DURATION_UNITS[match.group(2)]
    else:
        raise ConfigError(f"{where}: expected a duration like \"240s\", got {value!r}")
    if seconds <= 0:
        raise ConfigError(f"{where}: duration must be positive, got {value!r}")
    return seconds


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a table")
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _str(table: dict[str, Any], key: str, where: str, *, required: bool = False) -> str | None:
    if key not in table:
        if required:
            raise ConfigError(f"{where}: missing required key '{key}'")
        return None
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key}: expected a non-empty string")
    return value


def _str_list(table: dict[str, Any], key: str, where: str) -> list[str]:
    if key not in table:
        return []
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"{where}.{key}: expected a list of non-empty strings")
    return list(value)


# --------------------------------------------------------------------------- sections


def _load_project(raw: Any, root: Path) -> Project:
    table = _table(raw, "[project]")
    _check_keys(table, _PROJECT_KEYS, "[project]")
    name = _str(table, "name", "[project]", required=True)
    state_dir = _str(table, "state_dir", "[project]") or ".dop"
    default_environment = _str(table, "default_environment", "[project]", required=True)
    return Project(name=name, state_dir=root / state_dir, default_environment=default_environment)


def _load_environment(name: str, raw: Any, root: Path) -> Environment:
    where = f"[environments.{name}]"
    table = _table(raw, where)
    _check_keys(table, _ENVIRONMENT_KEYS, where)
    return Environment(
        dir=root / (_str(table, "dir", where) or "."),
        kubeconfig=_str(table, "kubeconfig", where, required=True),
        namespace=_str(table, "namespace", where, required=True),
        cluster_exists=_str(table, "cluster_exists", where),
        cluster_running=_str(table, "cluster_running", where),
        cluster_up=_str(table, "cluster_up", where),
        cluster_start=_str(table, "cluster_start", where),
        cluster_stop=_str(table, "cluster_stop", where),
        prepare=_str_list(table, "prepare", where),
        up=_str(table, "up", where),
        down=_str(table, "down", where),
        status=_str(table, "status", where),
        workloads=_str_list(table, "workloads", where),
        wait_timeout=parse_duration(table.get("wait_timeout", DEFAULT_WAIT_TIMEOUT), f"{where}.wait_timeout"),
        addresses=_str_list(table, "addresses", where),
    )


def _load_layer(component: str, layer: str, raw: Any) -> TestLayer:
    where = f"[components.{component}.test.{layer}]"
    if layer not in TEST_LAYERS:
        raise ConfigError(f"{where}: unknown test layer '{layer}' (expected one of {', '.join(TEST_LAYERS)})")
    if isinstance(raw, list):
        commands = _str_list({"commands": raw}, "commands", where)
        forwards: list[str] = []
    elif isinstance(raw, dict):
        _check_keys(raw, _LAYER_TABLE_KEYS, where)
        if "commands" not in raw:
            raise ConfigError(f"{where}: missing required key 'commands'")
        commands = _str_list(raw, "commands", where)
        forwards = _str_list(raw, "forwards", where)
    else:
        raise ConfigError(f"{where}: expected a list of commands or a table {{ forwards?, commands }}")
    if not commands:
        raise ConfigError(f"{where}: declares no commands")
    return TestLayer(forwards=forwards, commands=commands)


def _load_component(name: str, raw: Any, root: Path) -> Component:
    where = f"[components.{name}]"
    table = _table(raw, where)
    _check_keys(table, _COMPONENT_KEYS, where)
    kind = _str(table, "kind", where, required=True)
    if kind not in KINDS:
        raise ConfigError(f"{where}.kind: unknown kind '{kind}' (expected one of {', '.join(KINDS)})")
    env_raw = table.get("env", {})
    if not isinstance(env_raw, dict) or not all(isinstance(v, str) for v in env_raw.values()):
        raise ConfigError(f"{where}.env: expected a table of string values")
    tests_raw = _table(table.get("test", {}), f"{where}.test")
    component = Component(
        name=name,
        repo=root / _str(table, "repo", where, required=True),
        kind=kind,
        needs=_str_list(table, "needs", where),
        setup=_str(table, "setup", where),
        run=_str(table, "run", where),
        env=dict(env_raw),
        ready=_str(table, "ready", where),
        address=_str(table, "address", where),
        workloads=_str_list(table, "workloads", where),
        logs=_str(table, "logs", where),
        tests={layer: _load_layer(name, layer, value) for layer, value in tests_raw.items()},
    )
    if kind == "process" and component.run is None:
        raise ConfigError(f"{where}: a 'process' component needs 'run'")
    if kind == "cluster" and not component.workloads:
        raise ConfigError(f"{where}: a 'cluster' component needs 'workloads'")
    return component


def _check_graph(components: dict[str, Component]) -> None:
    for component in components.values():
        for need in component.needs:
            if need not in components:
                raise ConfigError(f"[components.{component.name}].needs: unknown component '{need}'")
            if need == component.name:
                raise ConfigError(f"[components.{component.name}].needs: a component cannot need itself")
    # Detect cycles with a coloured depth-first search.
    state: dict[str, int] = {}

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            cycle = path[path.index(name):] + [name]
            raise ConfigError(f"dependency cycle: {' -> '.join(cycle)}")
        state[name] = 1
        for need in components[name].needs:
            visit(need, path + [name])
        state[name] = 2

    for name in components:
        visit(name, [])


# --------------------------------------------------------------------------- public API


def load(path: Path) -> Config:
    """Read and validate a `dop.toml`; any problem raises `ConfigError`."""
    path = Path(path).resolve()
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"{path}: no such file") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None
    root = path.parent
    _check_keys(raw, _TOP_KEYS, path.name)
    if "project" not in raw:
        raise ConfigError(f"{path.name}: missing required table [project]")
    project = _load_project(raw["project"], root)
    environments = {
        name: _load_environment(name, value, root)
        for name, value in _table(raw.get("environments", {}), "[environments]").items()
    }
    if project.default_environment not in environments:
        raise ConfigError(
            f"[project].default_environment: '{project.default_environment}' is not declared under [environments]"
        )
    components = {
        name: _load_component(name, value, root)
        for name, value in _table(raw.get("components", {}), "[components]").items()
    }
    _check_graph(components)
    return Config(root=root, project=project, environments=environments, components=components)


def closure(config: Config, names: list[str]) -> list[Component]:
    """The named components and everything they need, dependencies first."""
    for name in names:
        if name not in config.components:
            raise ConfigError(f"unknown component '{name}' (declared: {', '.join(config.components) or 'none'})")
    ordered: list[Component] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        component = config.components[name]
        for need in component.needs:
            visit(need)
        ordered.append(component)

    for name in names:
        visit(name)
    return ordered
