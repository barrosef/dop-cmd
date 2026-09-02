# tests/test_runtime_compose.py
from pathlib import Path
from dop.runtime.compose import (
    build_up_command, build_stop_command, build_run_command,
    build_logs_command, build_ps_command, write_env_runtime,
)

def test_build_up_command():
    cmd = build_up_command(
        compose_file=Path("/ws/docker-compose.yml"),
        env_files=[Path("/ws/docker/.env"), Path("/ws/docker/.env.runtime")],
        services=["optum-support-be", "optum-support-fe"],
        wait=True, build=False,
    )
    assert cmd[0] == "docker"
    assert "compose" in cmd
    assert "up" in cmd
    assert "-d" in cmd
    assert "--wait" in cmd
    assert "optum-support-be" in cmd
    assert "optum-support-fe" in cmd
    assert "--env-file" in cmd

def test_build_up_with_build_and_recreate():
    cmd = build_up_command(
        compose_file=Path("/ws/dc.yml"),
        env_files=[],
        services=["app"],
        build=True, force_recreate=True,
    )
    assert "--build" in cmd
    assert "--force-recreate" in cmd

def test_build_stop_all():
    cmd = build_stop_command(compose_file=Path("/ws/dc.yml"), services=[])
    assert "stop" in cmd
    assert len([x for x in cmd if x not in ("docker","compose","-f","/ws/dc.yml","stop")]) == 0

def test_build_stop_specific():
    cmd = build_stop_command(compose_file=Path("/ws/dc.yml"), services=["app1"])
    assert "app1" in cmd

def test_build_logs_command():
    cmd = build_logs_command(
        compose_file=Path("/ws/dc.yml"),
        services=["app1", "app2"],
        follow=True, tail=50, since="5m",
    )
    assert "-f" in cmd
    assert "--tail" in cmd
    assert "50" in cmd
    assert "--since" in cmd
    assert "5m" in cmd

def test_build_run_command():
    cmd = build_run_command(
        compose_file=Path("/ws/dc.yml"),
        env_files=[Path("/ws/.env")],
        service="playwright-env",
        args=["-k", "test_smoke", "--headed"],
        profile="e2e",
        extra_env={"DISPLAY": ":0"},
    )
    assert "--rm" in cmd
    assert "--profile" in cmd
    assert "e2e" in cmd
    assert "playwright-env" in cmd
    assert "-k" in cmd

def test_build_ps_command():
    cmd = build_ps_command(compose_file=Path("/ws/dc.yml"))
    assert "ps" in cmd
    assert "--format" in cmd

def test_write_env_runtime(tmp_path):
    env = {"LIFESUPPORT_URL": "http://ls:8082", "FOO": "bar"}
    out = tmp_path / ".env.runtime"
    write_env_runtime(out, env)
    content = out.read_text()
    assert "LIFESUPPORT_URL=http://ls:8082" in content
    assert "FOO=bar" in content


def test_build_run_command_with_workdir():
    cmd = build_run_command(
        compose_file=Path("/ws/docker-compose.yml"),
        service="java-test",
        args=["mvn", "test"],
        profile="test",
        workdir="/workspace/test/aaa/demo",
    )
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
