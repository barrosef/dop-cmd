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
