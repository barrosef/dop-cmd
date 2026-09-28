"""§6 and B30: the config is loaded strictly; every error is exit 2, nothing attempted."""

import pytest

from dop.config import find_workspace, load, parse_env_file
from dop.outcome import UsageError

from .conftest import CONFIG, run_cli


def write(root, text):
    (root / "dop.toml").write_text(text)


def test_valid_config_loads(ws, root):
    assert ws.cluster.context == "k3d-test"
    assert ws.paths.env_file == root / "docker" / ".env"
    assert ws.repos["be"].dir == root / "repos" / "be"
    assert ws.apps["be"].calls[0].fallback == "https://api.remote.example"
    assert ws.apps["api"].build.command == ("sh", "-c", "mvn package")
    assert ws.apps["be"].build.command == ("mvn", "package")
    assert ws.apps["fe"].fallbacks() == {"be": "https://be.remote.example"}


@pytest.mark.parametrize("mutation, message", [
    (lambda c: c + "\n[extra]\nx = 1\n", "unknown key"),
    (lambda c: c.replace('node_root = "/workspace"', 'node_root = "/workspace"\nzone = "a"'), "unknown key"),
    (lambda c: c.replace('suite = "fe"', 'suite = "fe"\ncolour = "red"'), "unknown key"),
    (lambda c: c.replace('build = { image = "node:22", command', 'build = { image = "node:22", shell = "x", command'), "unknown key"),
    (lambda c: c.replace('context = "k3d-test"\n', ""), "missing required key"),
    (lambda c: c.replace('service = "solo"\n', ""), "missing required key"),
    (lambda c: c.replace('[runners]\nmaven = "maven:3"\n', "[runners]\n"), "missing required key"),
    (lambda c: c.replace('repo = "solo"', 'repo = "nowhere"'), "unknown repository"),
    (lambda c: c.replace('service = "solo"', 'service = "api"'), "duplicated service"),
    (lambda c: c + '\n[apps.api]\nrepo = "api"\n', "api"),  # TOML duplicate table
    (lambda c: c.replace('companions = ["fe"]', 'companions = ["ghost"]'), "unknown app"),
    (lambda c: c.replace('companions = ["fe"]', 'companions = ["fe", "fe"]'), "duplicated name"),
    (lambda c: c.replace('app = "api", key', 'app = "ghost", key'), "unknown app"),
    (lambda c: c.replace("{url:be}", "{url:ghost}"), "unknown app"),
    (lambda c: c.replace('kind = "frontend"', 'kind = "spa"'), "must be one of"),
    (lambda c: c.replace("port = 8095", 'port = "8095"'), "integer"),
    (lambda c: c.replace("[apps.solo]", "[apps.Solo]"), "DNS label"),
    (lambda c: c.replace('domain = "localhost"', 'domain = "LocalHost"'), "lower case"),
    (lambda c: c.replace('artifact = "dist"', 'artifact = "../dist"'), "relative path"),
    (lambda c: c.replace('env_file = "docker/.env"', 'env_file = "docker/missing.env"'), "does not exist"),
])
def test_config_errors_are_usage_errors(root, mutation, message):
    write(root, mutation(CONFIG))
    with pytest.raises(UsageError, match=message):
        load(root)


def test_config_error_exits_2_through_cli(root, fake):
    write(root, CONFIG + "\n[extra]\n")
    code, _ = run_cli(root, "status")
    assert code == 2
    assert fake.log("kubectl") == []  # nothing attempted


def test_missing_env_file_exits_2(root, fake):
    (root / "docker" / ".env").unlink()
    code, _ = run_cli(root, "status")
    assert code == 2


def test_find_workspace_walks_up(root):
    deep = root / "repos" / "be" / "src"
    deep.mkdir(parents=True)
    assert find_workspace(deep) == root


def test_no_workspace_is_usage_error(tmp_path):
    with pytest.raises(UsageError):
        find_workspace(tmp_path)


def test_env_file_parsing(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nexport B=\"two words\"\nC='x=y'\nnot a line\n\nD=\n")
    assert parse_env_file(p) == {"A": "1", "B": "two words", "C": "x=y", "D": ""}
