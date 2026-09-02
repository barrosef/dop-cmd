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
