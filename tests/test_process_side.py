"""The process side: ProcessManager, `process_up`, and the process hooks of down, reset and status.

Everything real here is a `/bin/sh` script in tmp_path; nothing touches the network or a real tool.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import time

import pytest

from dop.config import load
from dop.core import process
from dop.core.errors import ProcessError, ToolMissing
from dop.processes import ProcessManager
from dop.verbs import down, reset, status
from dop.verbs.up import process_up

from .fakes import Cluster, fast_waiter, mixed_runner, ws


def _args(dry_run=False, verbose=False):
    return argparse.Namespace(components=[], dry_run=dry_run, verbose=verbose, config=None)


@pytest.fixture
def env(proc_config, reap_owned):
    config = load(proc_config)
    pm = ProcessManager(config.project.state_dir)
    reap_owned((pm, config))
    return config, pm


def _app(config, **changes):
    return dataclasses.replace(config.components["app"], **changes)


def _gone(pid: int) -> bool:
    """Not running; a zombie counts as gone."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        return True


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _up_process(config, pm, component, **kw):
    kw.setdefault("probe", lambda url: False)
    kw.setdefault("waiter", fast_waiter)
    kw.setdefault("tool_check", lambda commands: None)
    return process_up(component, processes=pm, root=config.root, timeout=kw.pop("timeout", 5), dry_run=kw.pop("dry_run", False), **kw)


# ---------------------------------------------------------------- ProcessManager


def test_start_writes_pid_and_log_and_the_process_lives(env):
    config, pm = env
    app = _app(config, run="echo hello-from-app; sleep 30", env={"GREETING": "x"})
    pid = pm.start(app, root=config.root)
    assert pm.pid_path(app).read_text().strip() == str(pid)
    assert pm.log_path(app) == config.project.state_dir / "app.log"
    assert pm.alive(app) == pid
    assert _wait_for(lambda: "hello-from-app" in pm.log_path(app).read_text())
    assert pm.owned() == ["app"]


def test_start_runs_in_the_repo_with_env_merged_over_os_environ(env, monkeypatch):
    config, pm = env
    monkeypatch.setenv("DOP_TEST_INHERITED", "inherited")
    app = _app(config, run='echo "$PWD $DOP_TEST_INHERITED $DOP_TEST_OWN"; sleep 30', env={"DOP_TEST_OWN": "own"})
    pm.start(app, root=config.root)
    assert _wait_for(lambda: pm.log_path(app).read_text().strip() != "")
    assert pm.log_path(app).read_text().split() == [str(config.root / "repos/dop-app"), "inherited", "own"]


def test_start_dry_run_prints_and_writes_nothing(env, capsys):
    config, pm = env
    assert pm.start(_app(config, run="sleep 30"), root=config.root, dry_run=True) is None
    assert capsys.readouterr().out.strip() == "WOULD RUN sleep 30"
    assert pm.owned() == []


def test_alive_removes_a_stale_pid_file(env):
    config, pm = env
    app = _app(config)
    dead = os.fork()
    if dead == 0:
        os._exit(0)
    os.waitpid(dead, 0)
    pm.state_dir.mkdir(parents=True, exist_ok=True)
    pm.pid_path(app).write_text(f"{dead}\n")
    assert pm.alive(app, cleanup=False) is None
    assert pm.pid_path(app).exists()
    assert pm.alive(app) is None
    assert not pm.pid_path(app).exists()


def test_alive_is_none_without_a_pid_file(env):
    config, pm = env
    assert pm.alive(_app(config)) is None
    assert pm.stop(_app(config)) is False


def test_a_process_that_exits_is_not_alive(env):
    config, pm = env
    app = _app(config, run="exit 3")
    pm.start(app, root=config.root)
    assert _wait_for(lambda: pm.alive(app, cleanup=False) is None)  # reaped, not left a zombie
    assert pm.alive(app) is None and not pm.pid_path(app).exists()


def test_stop_ends_the_whole_group_including_a_child(env, tmp_path):
    config, pm = env
    child_file = tmp_path / "child.pid"
    app = _app(config, run=f"sleep 30 & echo $! > {child_file}; wait")
    pid = pm.start(app, root=config.root)
    assert _wait_for(lambda: child_file.exists() and child_file.read_text().strip())
    child = int(child_file.read_text())
    assert not _gone(child)
    assert pm.stop(app, grace=5) is True
    assert _wait_for(lambda: _gone(child))
    assert _gone(pid)
    assert not pm.pid_path(app).exists()
    assert pm.alive(app) is None


def test_stop_kills_what_ignores_sigterm_after_the_grace(env):
    config, pm = env
    app = _app(config, run="trap '' TERM; while :; do sleep 1; done")
    pid = pm.start(app, root=config.root)
    time.sleep(0.2)  # let the shell install the trap
    started = time.monotonic()
    assert pm.stop(app, grace=0.5) is True
    assert time.monotonic() - started >= 0.4
    assert _wait_for(lambda: _gone(pid))


# ---------------------------------------------------------------- process_up


def test_port_held_by_a_stranger_is_failed(env):
    config, pm = env
    calls = []
    with pytest.raises(ProcessError, match="port held by a process dop does not own"):
        _up_process(config, pm, config.components["app"], probe=lambda url: True,
                    runner=lambda *a, **k: calls.append(a) or process.Result(rc=0))
    assert calls == []
    assert pm.owned() == []


def test_already_running_is_done_and_starts_nothing(env):
    config, pm = env
    app = _app(config, run="sleep 30")
    pid = pm.start(app, root=config.root)
    started = []
    detail = _up_process(config, pm, app, probe=lambda url: True, runner=lambda *a, **k: started.append(a))
    assert detail == f"already running, pid {pid}"
    assert started == []
    assert pm.alive(app) == pid


def test_setup_failure_is_failed_and_nothing_starts(env):
    config, pm = env
    app = _app(config, setup="exit 3", run="sleep 30")
    with pytest.raises(ProcessError, match=r"setup failed \(rc 3\)"):
        _up_process(config, pm, app)
    assert pm.owned() == []


def test_setup_runs_in_the_repo_before_the_process(env, tmp_path):
    config, pm = env
    marker = tmp_path / "setup-ran"
    app = _app(config, setup=f"pwd > {marker}", run="test -s " + str(marker) + " && sleep 30", ready=None)
    _up_process(config, pm, app)
    assert marker.read_text().strip() == str(config.root / "repos/dop-app")
    assert pm.alive(app) is not None


def test_a_dead_pid_during_the_probe_is_failed_with_the_log_path(env):
    config, pm = env
    app = _app(config, setup=None, run="echo boom; exit 1")
    with pytest.raises(ProcessError) as raised:
        _up_process(config, pm, app, probe=lambda url: False)
    assert f"process exited (see {pm.log_path(app)})" == str(raised.value)
    assert "boom" in pm.log_path(app).read_text()


def test_a_dead_pid_is_never_ready_even_if_the_port_answers(env):
    config, pm = env
    app = _app(config, setup=None, run="exit 1")
    state = {"started": False}

    def probe(url):
        return state["started"]

    def waiter(fn, timeout, alive):
        state["started"] = True
        assert _wait_for(lambda: not alive())
        return False if not alive() else fn()

    with pytest.raises(ProcessError, match="process exited"):
        _up_process(config, pm, app, probe=probe, waiter=waiter)


def test_ready_is_done(env):
    config, pm = env
    app = _app(config, setup=None, run="sleep 30")
    detail = _up_process(config, pm, app, probe=lambda url: pm.alive(app, cleanup=False) is not None)
    assert detail.startswith("ready, pid ")
    assert pm.alive(app) == int(detail.rsplit(" ", 1)[1])


def test_not_ready_in_time_is_failed(env):
    config, pm = env
    app = _app(config, setup=None, run="sleep 30")
    with pytest.raises(ProcessError, match=r"not ready after 0\.3s"):
        _up_process(config, pm, app, timeout=0.3)
    assert pm.alive(app) is not None  # left running; `dop down` stops it


def test_without_a_ready_url_done_once_alive(env):
    config, pm = env
    app = _app(config, setup=None, run="sleep 30", ready=None)
    probed = []
    detail = _up_process(config, pm, app, probe=lambda url: probed.append(url) or False)
    assert detail.startswith("started, pid ") and probed == []


def test_without_a_ready_url_a_process_that_died_is_failed(env):
    config, pm = env
    app = _app(config, setup=None, run="exit 1", ready=None)

    def runner(cmd, **kw):
        result = process.run(cmd, **kw)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _gone(result.pid):
            time.sleep(0.02)
        return result

    with pytest.raises(ProcessError, match="process exited"):
        _up_process(config, pm, app, runner=runner)


def test_missing_tool_stops_before_anything_starts(env):
    config, pm = env

    def tools(commands):
        assert commands == ["true", "sleep 30"]
        raise ToolMissing("pnpm")

    with pytest.raises(ToolMissing):
        _up_process(config, pm, config.components["app"], tool_check=tools)
    assert pm.owned() == []


def test_process_up_dry_run(env, capsys):
    config, pm = env
    detail = _up_process(config, pm, config.components["app"], dry_run=True)
    assert capsys.readouterr().out.splitlines() == [
        "WOULD RUN true",
        "WOULD RUN sleep 30",
        "WOULD WAIT ready http://localhost:5173",
    ]
    assert detail == "" and pm.owned() == []


# ---------------------------------------------------------------- down, reset, status


def test_down_stops_owned_processes_then_the_cluster(env, capsys):
    config, pm = env
    app = config.components["app"]
    pid = pm.start(_app(config, run="sleep 30"), root=config.root)
    cluster = Cluster("running")
    seen = {}
    original = cluster.run

    def run(cmd, **kw):
        if cmd.endswith("cluster-stop"):
            seen["app_gone_at_cluster_stop"] = _gone(pid)
        return original(cmd, **kw)

    cluster.run = run
    rc = down.run(config, _args(), runner=cluster.run, kube_factory=cluster.kube, tool_check=lambda c: None, processes=pm)
    assert rc == 0
    assert seen == {"app_gone_at_cluster_stop": True}
    assert pm.alive(app) is None
    out = capsys.readouterr().out.splitlines()
    assert out.index("dop down: stopped app") < out.index("dop down: cluster stopped; data is kept")


def test_down_with_no_owned_process_prints_no_stopped_line(env, capsys):
    config, pm = env
    cluster = Cluster("running")
    down.run(config, _args(), runner=cluster.run, kube_factory=cluster.kube, tool_check=lambda c: None, processes=pm)
    assert "stopped app" not in capsys.readouterr().out


def test_down_dry_run_is_honest(env, capsys):
    config, pm = env
    pid = pm.start(_app(config, run="sleep 30"), root=config.root)
    cluster = Cluster("running")
    rc = down.run(config, _args(dry_run=True), runner=cluster.run, kube_factory=cluster.kube, processes=pm)
    out = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert "WOULD STOP app" in out and "WOULD RUN make -C repos/dop-infra cluster-stop" in out
    assert out.index("WOULD STOP app") < out.index("WOULD RUN make -C repos/dop-infra cluster-stop")
    assert not any("data is kept" in line or "stopped" in line for line in out)
    assert not _gone(pid)  # nothing was stopped


def test_reset_stops_processes_before_the_environment_down(env, capsys):
    config, pm = env
    pid = pm.start(_app(config, run="sleep 30"), root=config.root)
    cluster = Cluster("running")
    seen = {}
    original = mixed_runner(cluster)

    def run(cmd, **kw):
        if cmd == "make -C repos/dop-infra down":
            seen["gone_at_down"] = _gone(pid)
        return original(cmd, **kw)

    rc = reset.run(config, _args(), runner=run, kube_factory=cluster.kube, tool_check=lambda c: None,
                   ready_wait=lambda *a: True, processes=pm, probe=lambda url: pm.alive(config.components["app"], cleanup=False) is not None,
                   waiter=fast_waiter)
    assert rc == 0
    assert seen == {"gone_at_down": True}
    assert "dop reset: stopped app" in capsys.readouterr().out
    new_pid = pm.alive(config.components["app"])
    assert new_pid is not None and new_pid != pid  # the up that follows started it again


ALL = [ws(w, 1) for w in ["sts/postgres", "sts/nats", "sts/firebase", "deploy/secretmanager", "deploy/dop-core-serve", "deploy/dop-api"]]


def _status(config, pm, probe=True, dry_run=False):
    cluster = Cluster("running", workloads=ALL)
    return status.run(config, _args(dry_run=dry_run), runner=cluster.run, kube_factory=cluster.kube,
                      tool_check=lambda c: None, prober=lambda url, dry: probe, processes=pm)


def _app_line(out):
    return next(line for line in out.splitlines() if line.split()[:2] == ["app", "process"])


def test_status_process_down_is_not_ready(env, capsys):
    config, pm = env
    assert _status(config, pm) == 3
    assert _app_line(capsys.readouterr().out).split()[2] == "down"


def test_status_process_ready_shows_pid_and_log_and_exits_0(env, capsys):
    config, pm = env
    pid = pm.start(_app(config, run="sleep 30"), root=config.root)
    assert _status(config, pm) == 0
    out = capsys.readouterr().out
    assert _app_line(out).split()[:3] == ["app", "process", "ready"]
    assert f"pid {pid}" in out and str(pm.log_path(config.components["app"])) in out


def test_status_process_alive_but_probe_fails_is_not_ready(env, capsys):
    config, pm = env
    pm.start(_app(config, run="sleep 30"), root=config.root)
    assert _status(config, pm, probe=False) == 3
    assert _app_line(capsys.readouterr().out).split()[2:4] == ["not", "ready"]


def test_status_alive_without_a_ready_url_is_ready(env, capsys):
    config, pm = env
    app = _app(config, run="sleep 30", ready=None)
    pm.start(app, root=config.root)
    state, detail = status.process_state(app, processes=pm, prober=lambda url, dry: pytest.fail("probed"))
    assert state == "ready" and "pid" in detail


def test_status_changes_nothing_not_even_a_stale_pid_file(env):
    config, pm = env
    app = config.components["app"]
    pm.state_dir.mkdir(parents=True, exist_ok=True)
    pm.pid_path(app).write_text("999999999\n")
    _status(config, pm)
    assert pm.pid_path(app).exists()


# ---------------------------------------------------------------- S1 regressions (live validation)


def _up_app(config, pm, cluster, probe, **kw):
    from dop.verbs import up

    return up.run(
        config, argparse.Namespace(components=["app"], dry_run=False, verbose=False, config=None),
        runner=mixed_runner(cluster), kube_factory=cluster.kube, tool_check=lambda c: None,
        ready_wait=lambda *a: True, processes=pm, probe=probe, waiter=fast_waiter, **kw,
    )


def test_up_app_with_the_port_held_exits_1_not_partial(env, capsys):
    # Live: `dop up app` with a stranger on :5173 printed the right line but exited 3,
    # because core and bff (the closure, not asked for) were done.
    config, pm = env
    rc = _up_app(config, pm, Cluster("running"), probe=lambda url: True)
    assert rc == 1
    out = capsys.readouterr().out
    assert any(line.startswith("app") and "port held by a process dop does not own" in line for line in out.splitlines())
    assert pm.owned() == []


def test_up_app_whose_process_dies_exits_1(env, capsys):
    config, pm = env
    config = dataclasses.replace(
        config, components={**config.components, "app": _app(config, run="exit 7")}
    )
    rc = _up_app(config, pm, Cluster("running"), probe=lambda url: False)
    assert rc == 1
    assert "process exited" in capsys.readouterr().out


def test_up_everything_with_only_app_failing_is_partial(env):
    from dop.verbs import up

    config, pm = env
    cluster = Cluster("running")
    rc = up.run(
        config, _args(), runner=mixed_runner(cluster), kube_factory=cluster.kube, tool_check=lambda c: None,
        ready_wait=lambda *a: True, processes=pm, probe=lambda url: True, waiter=fast_waiter,
    )
    assert rc == 3


def test_down_checks_exactly_the_commands_it_runs(env):
    # S0c: `down` sliced `[:-1]` off a list that ends in "kubectl" and never checked
    # the kubeconfig command it hands to `cluster_stop`.
    config, pm = env
    seen = []
    cluster = Cluster("running")
    down.run(config, _args(), runner=cluster.run, kube_factory=cluster.kube, tool_check=seen.append, processes=pm)
    environment = config.environment
    assert seen == [[environment.cluster_exists, environment.cluster_running, environment.kubeconfig, environment.cluster_stop]]


def test_reset_checks_process_tools_before_down_destroys_data(env):
    # S0c: reset handed a no-op tool_check to bring_up, so a missing `pnpm` was
    # found (if at all) after `make down` had deleted the namespace.
    config, pm = env
    cluster = Cluster("running")

    def tools(commands):
        if any(cmd.startswith("true") or cmd.startswith("sleep") for cmd in commands):
            raise ToolMissing("sleep")

    with pytest.raises(ToolMissing):
        reset.run(config, _args(), runner=mixed_runner(cluster), kube_factory=cluster.kube, tool_check=tools,
                  ready_wait=lambda *a: True, processes=pm, probe=lambda url: False, waiter=fast_waiter)
    assert cluster.calls == []


def test_reset_hands_the_real_tool_check_to_the_up_that_follows(env):
    config, pm = env
    cluster = Cluster("running")
    seen = []
    app = config.components["app"]
    reset.run(config, _args(), runner=mixed_runner(cluster), kube_factory=cluster.kube, tool_check=seen.append,
              ready_wait=lambda *a: True, processes=pm,
              probe=lambda url: pm.alive(app, cleanup=False) is not None, waiter=fast_waiter)
    assert [app.setup, app.run] in seen  # process_up's own check ran with it
    assert app.setup in seen[0] and app.run in seen[0]  # and the upfront one covered it
