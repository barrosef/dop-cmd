from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dop import config as cfg
from dop.config import ConfigError, closure, find_config, load, parse_duration

from .conftest import EXAMPLE

MINIMAL = '''
[project]
name = "x"
default_environment = "local"
[environments.local]
kubeconfig = "echo /k"
namespace = "ns"
'''


def test_loads_the_spec_example(example_config: Path):
    config = load(example_config)
    root = example_config.parent
    assert config.root == root
    assert config.project.name == "dop"
    assert config.project.state_dir == root / ".dop"
    env = config.environment
    assert env.namespace == "dop-local"
    assert env.dir == root / "repos/dop-infra"
    assert env.wait_timeout == 240.0
    assert env.prepare == ["make -C repos/dop-infra images", "make -C repos/dop-infra image-runner"]
    assert set(config.components) == {"app", "bff", "core", "cli"}
    app = config.components["app"]
    assert app.kind == "process" and app.needs == ["bff"] and app.env == {"PORT": "5173"}
    assert app.repo == root / "repos/dop-app"
    assert app.tests["unit"] == cfg.TestLayer(forwards=[], commands=["pnpm typecheck", "pnpm -C artifacts/dop test"])
    core = config.components["core"]
    assert core.tests["contract"].forwards == ["svc/nats 4222:4222"]
    assert core.tests["contract"].commands == ["make test-contract-integration", "make proto-breaking"]
    assert config.components["cli"].kind == "none"


def test_defaults(write_config):
    config = load(write_config(MINIMAL))
    assert config.project.state_dir == config.root / ".dop"
    assert config.environment.wait_timeout == 240.0
    assert config.components == {}


@pytest.mark.parametrize(
    "value, seconds",
    [("240s", 240.0), ("4m", 240.0), ("1h", 3600.0), ("500ms", 0.5), ("30", 30.0), (15, 15.0)],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value, "w") == seconds


@pytest.mark.parametrize("value", ["", "fast", "-1s", "0s", True, "10d"])
def test_parse_duration_rejects(value):
    with pytest.raises(ConfigError):
        parse_duration(value, "w")


def _component(body: str, extra: str = "") -> str:
    return MINIMAL + extra + "\n[components.c]\n" + textwrap.dedent(body)


INVALID = {
    "unknown top-level key": MINIMAL + 'stray = 1\n',
    "unknown project key": MINIMAL.replace('name = "x"', 'name = "x"\nowner = "y"'),
    "unknown environment key": MINIMAL + 'context = "k3d"\n',
    "unknown component key": _component('repo = "r"\nkind = "none"\nimage = "x"\n'),
    "unknown layer table key": _component('repo = "r"\nkind = "none"\n[components.c.test.unit]\ncommands = ["a"]\nretry = 2\n'),
    "unknown kind": _component('repo = "r"\nkind = "docker"\n'),
    "missing kind": _component('repo = "r"\n'),
    "missing repo": _component('kind = "none"\n'),
    "unknown needs": _component('repo = "r"\nkind = "none"\nneeds = ["ghost"]\n'),
    "self need": _component('repo = "r"\nkind = "none"\nneeds = ["c"]\n'),
    "process without run": _component('repo = "r"\nkind = "process"\n'),
    "cluster without workloads": _component('repo = "r"\nkind = "cluster"\n'),
    "unknown test layer": _component('repo = "r"\nkind = "none"\n[components.c.test]\ne2e = ["a"]\n'),
    "layer neither list nor table": _component('repo = "r"\nkind = "none"\n[components.c.test]\nunit = "make test"\n'),
    "layer table without commands": _component('repo = "r"\nkind = "none"\n[components.c.test.unit]\nforwards = ["svc/a 1:1"]\n'),
    "layer with empty commands": _component('repo = "r"\nkind = "none"\n[components.c.test]\nunit = []\n'),
    "needs not a list": _component('repo = "r"\nkind = "none"\nneeds = "x"\n'),
    "env not strings": _component('repo = "r"\nkind = "none"\nenv = { PORT = 5173 }\n'),
    "bad wait_timeout": MINIMAL + 'wait_timeout = "soon"\n',
    "missing project": '[environments.local]\nkubeconfig = "k"\nnamespace = "n"\n',
    "missing kubeconfig": MINIMAL.replace('kubeconfig = "echo /k"\n', ""),
    "undeclared default environment": MINIMAL.replace('default_environment = "local"', 'default_environment = "gcp"'),
    "invalid toml": MINIMAL + "[[[",
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_every_validation_error_is_a_config_error(write_config, name):
    with pytest.raises(ConfigError):
        load(write_config(INVALID[name]))


def test_cycle_is_named(write_config):
    text = MINIMAL + '''
[components.a]
repo = "a"
kind = "none"
needs = ["b"]
[components.b]
repo = "b"
kind = "none"
needs = ["c"]
[components.c]
repo = "c"
kind = "none"
needs = ["a"]
'''
    with pytest.raises(ConfigError, match="cycle: a -> b -> c -> a"):
        load(write_config(text))


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load(tmp_path / "absent.toml")


def test_closure_in_dependency_order(example_config):
    config = load(example_config)
    assert [c.name for c in closure(config, ["app"])] == ["core", "bff", "app"]
    assert [c.name for c in closure(config, ["bff"])] == ["core", "bff"]
    assert [c.name for c in closure(config, ["cli", "app", "core"])] == ["cli", "core", "bff", "app"]
    assert [c.name for c in closure(config, list(config.components))] == ["core", "bff", "app", "cli"]


def test_closure_unknown_component(example_config):
    with pytest.raises(ConfigError, match="unknown component 'ghost'"):
        closure(load(example_config), ["ghost"])


def test_find_config_walks_up(example_config, monkeypatch):
    monkeypatch.delenv("DOP_CONFIG", raising=False)
    deep = example_config.parent / "repos" / "dop-app" / "src"
    deep.mkdir(parents=True)
    assert find_config(deep) == example_config.resolve()


def test_find_config_none_found(tmp_path, monkeypatch):
    monkeypatch.delenv("DOP_CONFIG", raising=False)
    lonely = tmp_path / "nowhere"
    lonely.mkdir()
    # tmp_path's parents might hold a dop.toml on a developer machine; only assert when they do not.
    if not any((p / "dop.toml").is_file() for p in lonely.resolve().parents):
        with pytest.raises(ConfigError):
            find_config(lonely)


def test_dop_config_overrides(tmp_path, write_config, monkeypatch):
    other = write_config(EXAMPLE, name="elsewhere.toml")
    monkeypatch.setenv("DOP_CONFIG", str(other))
    assert find_config(tmp_path) == other.resolve()


def test_dop_config_pointing_nowhere(tmp_path, monkeypatch):
    monkeypatch.setenv("DOP_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(ConfigError):
        find_config(tmp_path)
