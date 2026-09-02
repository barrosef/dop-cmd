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
