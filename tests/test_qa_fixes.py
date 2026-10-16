"""Regression tests for the QA findings of 2026-10-16 (spec §10.1); one per finding, named after it."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import stat
import subprocess
import sys

import pytest

from dop import cli
from dop.config import ConfigError, find_config, load, parse_duration
from dop.core import process
from dop.core.errors import ToolMissing
from dop.processes import ProcessManager
from dop.verbs import logs, up

from .conftest import EXAMPLE
from .fakes import Cluster, fast_waiter, mixed_runner
from .test_config import MINIMAL, _component
from .test_process_side import _app, _args, _up_process, env  # noqa: F401 - `env` is a fixture


# ------------------------------------------------------------------ D1


def test_qa_d1_a_directory_as_config_is_a_config_error(tmp_path, capsys):
    with pytest.raises(ConfigError, match="directory"):
        load(tmp_path)
    assert cli.main(["--config", str(tmp_path), "status"]) == 2
    err = capsys.readouterr().err
    assert str(tmp_path) in err and "Traceback" not in err


def test_qa_d1_not_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "dop.toml"
    path.write_bytes(b'[project]\nname = "\xff\xfe"\n')
    with pytest.raises(ConfigError, match="UTF-8"):
        load(path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads anything")
def test_qa_d1_unreadable_is_a_config_error(tmp_path):
    path = tmp_path / "dop.toml"
    path.write_text(MINIMAL)
    path.chmod(0)
    try:
        with pytest.raises(ConfigError, match="permission denied"):
            load(path)
    finally:
        path.chmod(0o644)


# ------------------------------------------------------------------ D2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -3, "nan", "inf", "", "abc", 10**400])
def test_qa_d2_parse_duration_rejects_non_finite_and_non_positive(value):
    with pytest.raises(ConfigError):
        parse_duration(value, "w")


@pytest.mark.parametrize("literal", ["nan", "inf", "-inf"])
def test_qa_d2_toml_float_wait_timeout_is_rejected(write_config, literal):
    with pytest.raises(ConfigError, match="wait_timeout"):
        load(write_config(MINIMAL + f"wait_timeout = {literal}\n"))


# ------------------------------------------------------------------ D3


@pytest.mark.parametrize("verb", ["up", "reset", "down"])
def test_qa_d3_dry_run_creates_nothing(proc_config, verb, capsys):
    root = proc_config.parent
    assert cli.main(["--config", str(proc_config), "--dry-run", verb]) == 0
    assert not (root / ".dop").exists()
    assert sorted(p.name for p in root.iterdir()) == ["dop.toml", "repos"]


def test_qa_d3_start_in_dry_run_does_not_create_the_state_dir(env):  # noqa: F811
    config, pm = env
    assert pm.start(config.components["app"], root=config.root, dry_run=True) is None
    assert not pm.state_dir.exists()


def test_qa_d3_dry_run_takes_no_lock(proc_config):
    config = load(proc_config)
    pm = ProcessManager(config.project.state_dir)
    cluster = Cluster("running")
    rc = up.run(
        config, _args(dry_run=True), runner=cluster.run, kube_factory=cluster.kube, processes=pm,
    )
    assert rc == 0 and not pm.lock_path.exists() and not pm.state_dir.exists()


# ------------------------------------------------------------------ D4


def test_qa_d4_relative_executable_resolves_against_the_command_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    tool = repo / "bin" / "serve"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    process.require_tools(["./bin/serve --port 1"], cwd=repo)  # exists in the repo, not in dop's cwd
    with pytest.raises(ToolMissing):
        process.require_tools(["./bin/serve"], cwd=elsewhere)
    with pytest.raises(ToolMissing):
        process.require_tools(["./bin/absent"], cwd=repo)


def test_qa_d4_a_non_executable_file_is_missing(tmp_path):
    (tmp_path / "x.sh").write_text("#!/bin/sh\n")
    (tmp_path / "x.sh").chmod(stat.S_IRUSR | stat.S_IWUSR)
    with pytest.raises(ToolMissing):
        process.require_tools(["./x.sh"], cwd=tmp_path)
    (tmp_path / "d").mkdir()
    with pytest.raises(ToolMissing):
        process.require_tools(["./d"], cwd=tmp_path)


def test_qa_d4_process_up_checks_a_relative_run_in_the_repo(env):  # noqa: F811
    config, pm = env
    script = config.components["app"].repo / "serve.sh"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    app = _app(config, setup=None, run="./serve.sh")
    from dop.verbs.up import process_up

    out = process_up(app, processes=pm, root=config.root, timeout=5, dry_run=False,
                     probe=lambda url: pm.alive(app, cleanup=False) is not None, waiter=fast_waiter)
    assert out.startswith("ready")


# ------------------------------------------------------------------ D5


def test_qa_d5_repo_must_exist_and_be_a_directory(write_config, tmp_path):
    body = MINIMAL + '[components.c]\nrepo = "nowhere"\nkind = "none"\n'
    path = write_config(body)
    (tmp_path / "nowhere").rmdir()
    with pytest.raises(ConfigError, match="repo"):
        load(path)
    (tmp_path / "nowhere").write_text("a file")
    with pytest.raises(ConfigError, match="repo"):
        load(path)


# ------------------------------------------------------------------ W1


def test_qa_w1_not_ready_stops_the_group_and_removes_the_pid_file(env):  # noqa: F811
    from dop.core.errors import ProcessError

    config, pm = env
    app = _app(config, setup=None, run="sleep 30")
    with pytest.raises(ProcessError, match="not ready"):
        _up_process(config, pm, app, timeout=0.3)
    assert pm.alive(app) is None and not pm.pid_path(app).exists()


def test_qa_w1_already_running_but_not_answering_is_not_ready(env, capsys):  # noqa: F811
    config, pm = env
    app = _app(config, setup=None, run="sleep 30")
    pm.start(app, root=config.root)
    from dop.core.errors import ProcessError

    with pytest.raises(ProcessError, match="not ready"):
        _up_process(config, pm, app, probe=lambda url: False)
    assert pm.alive(app) is not None  # not ours to kill here: it was already running


def test_qa_w1_already_running_and_answering_is_done(env):  # noqa: F811
    config, pm = env
    app = _app(config, setup=None, run="sleep 30")
    pid = pm.start(app, root=config.root)
    assert _up_process(config, pm, app, probe=lambda url: True) == f"already running, pid {pid}"


def test_qa_w1_asked_unit_not_answering_exits_1(env, capsys):  # noqa: F811
    config, pm = env
    app = config.components["app"]
    pm.start(_app(config, run="sleep 30"), root=config.root)
    cluster = Cluster("running")
    rc = up.run(
        config, argparse.Namespace(components=["app"], dry_run=False, verbose=False, config=None),
        runner=mixed_runner(cluster), kube_factory=cluster.kube, tool_check=lambda c: None,
        ready_wait=lambda *a: True, processes=pm, probe=lambda url: False, waiter=fast_waiter,
    )
    assert rc == 1
    assert "not ready" in capsys.readouterr().out
    assert pm.alive(app) is not None


# ------------------------------------------------------------------ W3


def test_qa_w3_pid_file_carries_identity(env):  # noqa: F811
    config, pm = env
    app = _app(config, run="sleep 30")
    pid = pm.start(app, root=config.root)
    record = json.loads(pm.pid_path(app).read_text())
    assert record["pid"] == pid and record["exe"] == "sleep"
    assert record["lstart"]  # ps exists on Linux and macOS


def test_qa_w3_a_reused_pid_is_stale_not_ours(env):  # noqa: F811
    config, pm = env
    app = _app(config, run="sleep 30")
    pid = pm.start(app, root=config.root)
    assert pm.alive(app) == pid
    stranger = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        # The file says "pnpm", the pid is a `sleep`: a reused number, not our process.
        pm.pid_path(app).write_text(json.dumps({"pid": stranger.pid, "exe": "pnpm", "exes": ["pnpm"]}))
        assert pm.alive(app) is None
        assert not pm.pid_path(app).exists()
        assert stranger.poll() is None  # and nothing was signalled
    finally:
        stranger.kill()
        stranger.wait()
        process.kill_group(pid, grace=1)


def test_qa_w3_a_changed_start_time_is_stale(env):  # noqa: F811
    config, pm = env
    app = _app(config, run="sleep 30")
    pid = pm.start(app, root=config.root)
    record = json.loads(pm.pid_path(app).read_text())
    record["lstart"] = "Mon Jan  1 00:00:00 2001"
    pm.pid_path(app).write_text(json.dumps(record))
    assert pm.alive(app) is None
    process.kill_group(pid, grace=1)


# ------------------------------------------------------------------ W4


def test_qa_w4_logs_of_a_dead_process_prints_the_tail_and_exits_3(env, capsys):  # noqa: F811
    config, pm = env
    app = config.components["app"]
    pm.state_dir.mkdir(parents=True)
    pm.log_path(app).write_text("".join(f"line {i}\n" for i in range(30)))
    seen = []
    rc = logs.run(config, argparse.Namespace(component="app", dry_run=False, verbose=False, config=None),
                  runner=lambda *a, **k: seen.append(a) or process.Result(rc=0), processes=pm)
    out = capsys.readouterr().out.splitlines()
    assert rc == 3 and seen == []  # never `tail -f` a dead log
    assert out[:20] == [f"line {i}" for i in range(10, 30)]
    assert out[-1] == "dop logs: app is not running"


def test_qa_w4_never_started_stays_never_started(env, capsys):  # noqa: F811
    config, pm = env
    rc = logs.run(config, argparse.Namespace(component="app", dry_run=False, verbose=False, config=None), processes=pm)
    assert rc == 3 and "never started" in capsys.readouterr().out


# ------------------------------------------------------------------ W5


def test_qa_w5_dry_run_of_a_running_process_says_already_running(env, capsys):  # noqa: F811
    config, pm = env
    app = config.components["app"]
    pid = pm.start(_app(config, run="sleep 30"), root=config.root)
    cluster = Cluster("running")
    rc = up.run(
        config, argparse.Namespace(components=["app"], dry_run=True, verbose=False, config=None),
        runner=mixed_runner(cluster), kube_factory=cluster.kube, processes=pm,
    )
    out = capsys.readouterr().out
    assert rc == 0 and f"already running, pid {pid}" in out
    assert "WOULD RUN true" not in out and "WOULD RUN sleep 30" not in out
    assert pm.alive(app, cleanup=False) == pid


# ------------------------------------------------------------------ W6


def _cluster_comp(body: str) -> str:
    return MINIMAL + "[components.c]\nrepo = \"r\"\n" + body


W6_INVALID = {
    "none with run": 'kind = "none"\nrun = "x"\n',
    "none with env": 'kind = "none"\nenv = { A = "1" }\n',
    "none with ready": 'kind = "none"\nready = "http://x"\n',
    "none with address": 'kind = "none"\naddress = "http://x"\n',
    "none with workloads": 'kind = "none"\nworkloads = ["deploy/a"]\n',
    "none with logs": 'kind = "none"\nlogs = "x"\n',
    "process with workloads": 'kind = "process"\nrun = "x"\nworkloads = ["deploy/a"]\n',
    "process with logs": 'kind = "process"\nrun = "x"\nlogs = "x"\n',
    "cluster with run": 'kind = "cluster"\nworkloads = ["deploy/a"]\nrun = "x"\n',
    "cluster with env": 'kind = "cluster"\nworkloads = ["deploy/a"]\nenv = { A = "1" }\n',
    "workload without kind": 'kind = "cluster"\nworkloads = ["a"]\n',
    "workload with bad kind": 'kind = "cluster"\nworkloads = ["pod/a"]\n',
    "workload uppercase": 'kind = "cluster"\nworkloads = ["deploy/A"]\n',
    "workload with space": 'kind = "cluster"\nworkloads = ["deploy/a b"]\n',
}


@pytest.mark.parametrize("name", sorted(W6_INVALID))
def test_qa_w6_kind_specific_keys_and_workload_shape(write_config, name):
    with pytest.raises(ConfigError):
        load(write_config(_cluster_comp(W6_INVALID[name])))


@pytest.mark.parametrize("ref", ["deploy/a", "deployment/a-b1", "sts/postgres", "statefulset/n"])
def test_qa_w6_valid_workloads_pass(write_config, ref):
    load(write_config(_cluster_comp(f'kind = "cluster"\nworkloads = ["{ref}"]\n')))


def test_qa_w6_duplicate_needs_are_rejected(write_config):
    text = MINIMAL + '[components.a]\nrepo = "a"\nkind = "none"\n[components.b]\nrepo = "b"\nkind = "none"\nneeds = ["a", "a"]\n'
    with pytest.raises(ConfigError, match="duplicate"):
        load(write_config(text))


def test_qa_w6_dir_is_no_longer_an_environment_key(write_config):
    with pytest.raises(ConfigError, match="dir"):
        load(write_config(MINIMAL.replace('namespace = "ns"', 'namespace = "ns"\ndir = "."')))


@pytest.mark.parametrize(
    "key", ["kubeconfig", "namespace", "up", "down", "cluster_exists", "cluster_running", "cluster_up", "cluster_start", "cluster_stop"]
)
def test_qa_w6_environment_requires(write_config, key):
    text = "\n".join(line for line in MINIMAL.splitlines() if not line.startswith(f"{key} = ")) + "\n"
    with pytest.raises(ConfigError, match=key):
        load(write_config(text))


def test_qa_w6_the_optional_environment_keys_stay_optional(write_config):
    env = load(write_config(MINIMAL)).environment
    assert env.status is None and env.prepare == [] and env.workloads == [] and env.addresses == []


def test_qa_w6_the_spec_example_still_loads(example_config):
    load(example_config)
    assert "\ndir =" not in EXAMPLE


# ------------------------------------------------------------------ BrokenPipe, os.environ, empty path


def test_qa_brokenpipe_is_exit_0(example_config, monkeypatch, capsys):
    def boom(config, args):
        raise BrokenPipeError

    monkeypatch.setattr("dop.verbs.status.run", boom)
    assert cli.main(["--config", str(example_config), "status"]) == 0
    assert "Traceback" not in capsys.readouterr().err


def test_qa_brokenpipe_real_pipe_leaves_no_traceback(example_config, tmp_path):
    # `dop … | head -0`: the reader is gone before dop writes a byte.
    script = (
        "import sys; from dop import cli; sys.exit(cli.main(['--config', %r, '--dry-run', 'up']))" % str(example_config)
    )
    reader = subprocess.Popen(["true"], stdin=subprocess.PIPE)
    reader.wait()
    done = subprocess.run(
        [sys.executable, "-c", script], stdout=reader.stdin, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(cli.__file__).parent.parent)},
    )
    assert "Traceback" not in done.stderr


def test_qa_a_command_with_os_environ_runs(tmp_path):
    result = process.run("echo os.environ", cwd=tmp_path, mode="capture")
    assert result.rc == 0 and result.stdout.strip() == "os.environ"
    assert process.run("true # os.environ", cwd=tmp_path, mode="stream").rc == 0


def test_qa_output_guard_still_applies_to_captured_failures(tmp_path):
    from dop.core.errors import SecurityViolationError

    with pytest.raises(SecurityViolationError):
        process.run("echo os.environ; exit 3", cwd=tmp_path, mode="capture")


def test_qa_empty_config_path_flag_is_exit_2(capsys):
    assert cli.main(["--config", "", "status"]) == 2
    assert "empty config path" in capsys.readouterr().err


def test_qa_empty_dop_config_env_is_exit_2(monkeypatch, capsys):
    monkeypatch.setenv("DOP_CONFIG", "")
    with pytest.raises(ConfigError, match="empty config path"):
        find_config()
    assert cli.main(["status"]) == 2
    assert "empty config path" in capsys.readouterr().err
