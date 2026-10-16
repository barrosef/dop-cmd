from __future__ import annotations

import os
import time

import pytest

from dop.core.errors import ToolMissing
from dop.core.process import executable_of, kill_group, require_tools, run


def _alive(pid: int) -> bool:
    try:
        finished, _ = os.waitpid(pid, os.WNOHANG)
        if finished == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stream_returns_rc_and_runs_in_cwd(tmp_path):
    result = run("pwd > where.txt; exit 3", cwd=tmp_path, mode="stream")
    assert result.rc == 3
    assert (tmp_path / "where.txt").read_text().strip() == str(tmp_path)


def test_capture_returns_stdout_and_env(tmp_path):
    result = run('printf "%s" "$DOP_TEST_VAR"', cwd=tmp_path, env={"DOP_TEST_VAR": "hello"}, mode="capture")
    assert result.rc == 0
    assert result.stdout == "hello"


def test_capture_keeps_the_inherited_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DOP_INHERITED", "yes")
    result = run('printf "%s-%s" "$DOP_INHERITED" "$EXTRA"', cwd=tmp_path, env={"EXTRA": "x"}, mode="capture")
    assert result.stdout == "yes-x"


def test_capture_failure_output_is_redacted(tmp_path):
    result = run('echo "TOKEN=supersecret"; echo "Authorization: Bearer abc.def" >&2; exit 4', cwd=tmp_path, mode="capture")
    assert result.rc == 4
    assert "supersecret" not in result.stdout
    assert "abc.def" not in result.stdout
    assert "TOKEN=***" in result.stdout
    assert "Bearer [REDACTED]" in result.stdout


def test_detach_writes_log_and_kill_group_ends_it(tmp_path):
    log = tmp_path / "logs" / "proc.log"
    result = run("echo started; sleep 30 & sleep 30; wait", cwd=tmp_path, mode="detach", log_file=log)
    pid = result.pid
    assert result.rc == 0 and isinstance(pid, int)
    assert os.getpgid(pid) == pid  # its own session and group
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "started" not in log.read_text():
        time.sleep(0.05)
    assert "started" in log.read_text()
    assert _alive(pid)
    kill_group(pid, grace=2)
    assert not _alive(pid)
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_kill_group_escalates_to_sigkill(tmp_path):
    log = tmp_path / "stubborn.log"
    result = run("trap '' TERM; echo ready; while :; do sleep 0.1; done", cwd=tmp_path, mode="detach", log_file=log)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "ready" not in log.read_text():
        time.sleep(0.05)
    started = time.monotonic()
    kill_group(result.pid, grace=0.5)
    assert time.monotonic() - started < 5
    assert not _alive(result.pid)


def test_kill_group_on_a_gone_pid_is_quiet(tmp_path):
    result = run("true", cwd=tmp_path, mode="detach", log_file=tmp_path / "t.log")
    time.sleep(0.2)
    os.waitpid(result.pid, 0)
    kill_group(result.pid, grace=0.1)


@pytest.mark.parametrize("pid", [0, 1, -5])
def test_kill_group_refuses_dangerous_pids(pid):
    with pytest.raises(ValueError):
        kill_group(pid)


def test_detach_needs_a_log_file(tmp_path):
    with pytest.raises(ValueError):
        run("true", cwd=tmp_path, mode="detach")


def test_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        run("true", cwd=tmp_path, mode="background")


@pytest.mark.parametrize("mode", ["stream", "capture", "detach"])
def test_dry_run_prints_and_runs_nothing(tmp_path, capsys, mode):
    marker = tmp_path / "ran"
    result = run(f"touch {marker}", cwd=tmp_path, mode=mode, log_file=tmp_path / "l.log", dry_run=True)
    assert result.rc == 0 and result.pid is None
    assert not marker.exists()
    assert capsys.readouterr().out == f"WOULD RUN touch {marker}\n"


def test_executable_of():
    assert executable_of("make -C repos/x up") == "make"
    assert executable_of("FOO=1 BAR=2 pnpm dev") == "pnpm"
    assert executable_of("/bin/sh -c true") == "/bin/sh"
    assert executable_of("") is None


def test_require_tools(tmp_path):
    require_tools(["sh -c true", "cd somewhere", "/bin/sh"])
    with pytest.raises(ToolMissing) as excinfo:
        require_tools(["sh -c true", "k3d-definitely-not-installed cluster list"])
    assert excinfo.value.tool == "k3d-definitely-not-installed"
