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
