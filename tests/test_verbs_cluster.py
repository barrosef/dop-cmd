"""`up`, `down`, `reset` and `status` against a scripted cluster; nothing real runs."""

from __future__ import annotations

import argparse

import pytest

from dop.config import ConfigError, load
from dop.core.errors import ToolMissing
from dop.processes import ProcessManager
from dop.verbs import down, reset, status, up

from .fakes import EXISTS, KUBECONFIG, KUBECONFIG_CMD, RUNNING, Cluster, ws

ENV_WORKLOADS = ["sts/postgres", "sts/nats", "sts/firebase", "deploy/secretmanager"]


def _args(components=(), dry_run=False, verbose=False):
    return argparse.Namespace(components=list(components), dry_run=dry_run, verbose=verbose, config=None)


class Ready:
    def __init__(self, answers=True):
        self.answers = answers
        self.probed = []

    def __call__(self, url, timeout, dry_run=False):
        self.probed.append(url)
        return self.answers


def _up(config, cluster, *components, dry_run=False, ready=None, tools=None):
    return up.run(
        config, _args(components, dry_run=dry_run),
        runner=cluster.run, kube_factory=cluster.kube,
        tool_check=tools or (lambda commands: None), ready_wait=ready or Ready(),
    )


# --------------------------------------------------------------------------- up


def test_up_core_waits_environment_then_core(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    assert _up(config, cluster, "core") == 0
    assert cluster.commands() == [EXISTS, RUNNING, KUBECONFIG_CMD, "make -C repos/dop-infra up"]
    assert cluster.env_of("make -C repos/dop-infra up") == [KUBECONFIG]
    assert cluster.waits() == ENV_WORKLOADS + ["deploy/dop-core-serve"]
    out = capsys.readouterr().out
    assert "environment  done" in out and "core" in out and "http://api.localtest.me:8080" in out


def test_up_bff_waits_core_before_bff_and_probes(example_config):
    config = load(example_config)
    cluster = Cluster("running")
    ready = Ready()
    assert _up(config, cluster, "bff", ready=ready) == 0
    assert cluster.waits() == ENV_WORKLOADS + ["deploy/dop-core-serve", "deploy/dop-api"]
    assert ready.probed == ["http://api.localtest.me:8080/healthz"]
    assert not any("jaeger" in w for w in cluster.waits())


def test_up_app_closure_and_process_is_reported_failed_until_s0c(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    assert _up(config, cluster, "app") == 3
    assert cluster.waits() == ENV_WORKLOADS + ["deploy/dop-core-serve", "deploy/dop-api"]
    lines = capsys.readouterr().out.splitlines()
    app = next(line for line in lines if line.startswith("app"))
    assert "failed" in app and "S0c" in app and "http://localhost:5173" in app


def test_up_no_args_brings_everything_in_order(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    assert _up(config, cluster) == 3  # app's process side is S0c
    assert cluster.waits() == ENV_WORKLOADS + ["deploy/dop-core-serve", "deploy/dop-api"]
    out = capsys.readouterr().out
    assert "nothing to bring up" in out
    units = [line.split()[0] for line in out.splitlines() if line and not line.startswith(("addresses", " "))]
    assert units == ["environment", "core", "bff", "app", "cli"]


def test_up_none_component_needs_no_environment(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("absent")
    assert _up(config, cluster, "cli") == 0
    assert cluster.calls == []
    assert "nothing to bring up" in capsys.readouterr().out


def test_up_absent_cluster_creates_and_prepares(example_config):
    config = load(example_config)
    cluster = Cluster("absent")
    assert _up(config, cluster, "core") == 0
    assert cluster.commands()[:5] == [
        EXISTS,
        "make -C repos/dop-infra cluster-up",
        KUBECONFIG_CMD,
        "make -C repos/dop-infra images",
        "make -C repos/dop-infra image-runner",
    ]
    assert cluster.commands()[-1] == "make -C repos/dop-infra up"


def test_up_environment_failure_exits_1_and_skips_components(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running", not_ready={"sts/postgres"})
    assert _up(config, cluster, "bff") == 1
    assert cluster.waits() == ["sts/postgres"]
    out = capsys.readouterr().out
    assert "environment  failed" in out and "skipped" in out


def test_up_all_components_failed_exits_1(example_config):
    config = load(example_config)
    cluster = Cluster("running", not_ready={"deploy/dop-core-serve"})
    assert _up(config, cluster, "core") == 1


def test_up_partial_exits_3_and_dependants_skipped(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    assert _up(config, cluster, "bff", "cli", ready=Ready(False)) == 3
    out = capsys.readouterr().out
    bff = next(line for line in out.splitlines() if line.startswith("bff"))
    assert "failed" in bff and "healthz" in bff
    cluster2 = Cluster("running", not_ready={"deploy/dop-core-serve"})
    capsys.readouterr()
    assert _up(config, cluster2, "bff") == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("bff") and "skipped" in line and "needs core" in line for line in lines)


def test_up_dry_run_prints_and_parses_nothing(example_config, capsys, tmp_path):
    config = load(example_config)
    cluster = Cluster("absent")
    assert up.run(config, _args(["bff"], dry_run=True), runner=cluster.run, kube_factory=cluster.kube) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:4] == [
        f"WOULD RUN {EXISTS}",
        f"WOULD RUN {RUNNING}",
        f"WOULD RUN {KUBECONFIG_CMD}",
        "WOULD RUN make -C repos/dop-infra up",
    ]
    assert [line for line in out if line.startswith("WOULD WAIT")] == [
        *(f"WOULD WAIT rollout {w}" for w in ENV_WORKLOADS),
        "WOULD WAIT rollout deploy/dop-core-serve",
        "WOULD WAIT rollout deploy/dop-api",
        "WOULD WAIT ready http://api.localtest.me:8080/healthz",
    ]
    assert "cluster-up" not in " ".join(out)  # dry-run assumes running, parses nothing
    assert not (config.project.state_dir / "lock").exists()


def test_up_unknown_component_is_config_error(example_config):
    with pytest.raises(ConfigError):
        _up(load(example_config), Cluster(), "ghost")


def test_up_missing_tool_raises_before_anything_runs(example_config):
    config = load(example_config)
    cluster = Cluster()

    def tools(commands):
        assert "kubectl" in commands and KUBECONFIG_CMD in commands
        raise ToolMissing("k3d")

    with pytest.raises(ToolMissing):
        _up(config, cluster, "core", tools=tools)
    assert cluster.calls == []


def test_up_takes_and_releases_the_lock(example_config):
    config = load(example_config)
    seen = []
    cluster = Cluster("running")
    original = cluster.run

    def run(cmd, **kw):
        seen.append((config.project.state_dir / "lock").exists())
        return original(cmd, **kw)

    cluster.run = run
    assert _up(config, cluster, "core") == 0
    assert seen and all(seen)
    assert not (config.project.state_dir / "lock").exists()


def test_up_refuses_when_another_dop_holds_the_lock(example_config, capsys, monkeypatch):
    config = load(example_config)
    config.project.state_dir.mkdir(parents=True)
    (config.project.state_dir / "lock").write_text("1\n")  # pid 1 is always alive
    cluster = Cluster("running")
    assert _up(config, cluster, "core") == 1
    assert "another dop is running (pid 1)" in capsys.readouterr().out
    assert cluster.calls == []


# --------------------------------------------------------------------------- down


def _down(config, cluster, dry_run=False):
    return down.run(config, _args(dry_run=dry_run), runner=cluster.run, kube_factory=cluster.kube,
                    tool_check=lambda commands: None)


def test_down_stops_the_cluster_with_kubeconfig(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    assert _down(config, cluster) == 0
    assert cluster.commands()[-1] == "make -C repos/dop-infra cluster-stop"
    assert cluster.env_of("make -C repos/dop-infra cluster-stop") == [KUBECONFIG]
    out = capsys.readouterr().out
    assert "S0c" in out and "data is kept" in out
    assert "make -C repos/dop-infra down" not in cluster.commands()


@pytest.mark.parametrize("state", ["absent", "stopped"])
def test_down_with_nothing_to_stop_is_ok(example_config, state):
    cluster = Cluster(state)
    assert _down(load(example_config), cluster) == 0
    assert "make -C repos/dop-infra cluster-stop" not in cluster.commands()


def test_down_failure_exits_1(example_config):
    cluster = Cluster("running", fail={"make -C repos/dop-infra cluster-stop"})
    assert _down(load(example_config), cluster) == 1


def test_down_dry_run(example_config, capsys):
    cluster = Cluster("running")
    assert _down(load(example_config), cluster, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "WOULD RUN make -C repos/dop-infra cluster-stop" in out


def test_down_refuses_a_held_lock(example_config, capsys):
    config = load(example_config)
    config.project.state_dir.mkdir(parents=True)
    (config.project.state_dir / "lock").write_text("1\n")
    cluster = Cluster("running")
    assert _down(config, cluster) == 1
    assert cluster.calls == []


# --------------------------------------------------------------------------- reset


def test_reset_runs_down_then_the_whole_up(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running")
    rc = reset.run(config, _args(), runner=cluster.run, kube_factory=cluster.kube,
                   tool_check=lambda commands: None, ready_wait=Ready())
    assert rc == 3  # app's process side is S0c
    commands = cluster.commands()
    assert commands.index("make -C repos/dop-infra down") < commands.index("make -C repos/dop-infra up")
    assert cluster.env_of("make -C repos/dop-infra down") == [KUBECONFIG]
    assert cluster.waits() == ENV_WORKLOADS + ["deploy/dop-core-serve", "deploy/dop-api"]
    assert "S0c" in capsys.readouterr().out
    assert not (config.project.state_dir / "lock").exists()


def test_reset_starts_a_stopped_cluster_before_down(example_config):
    cluster = Cluster("stopped")
    reset.run(load(example_config), _args(), runner=cluster.run, kube_factory=cluster.kube,
              tool_check=lambda commands: None, ready_wait=Ready())
    commands = cluster.commands()
    assert commands.index("k3d cluster start dop-local") < commands.index("make -C repos/dop-infra down")


def test_reset_down_failure_exits_1_without_up(example_config):
    cluster = Cluster("running", fail={"make -C repos/dop-infra down"})
    rc = reset.run(load(example_config), _args(), runner=cluster.run, kube_factory=cluster.kube,
                   tool_check=lambda commands: None, ready_wait=Ready())
    assert rc == 1
    assert "make -C repos/dop-infra up" not in cluster.commands()


# --------------------------------------------------------------------------- status

ALL_READY = [ws(w, 1) for w in ENV_WORKLOADS] + [ws("deploy/dop-core-serve", 1), ws("deploy/dop-api", 1)]


def _status(config, cluster, verbose=False, dry_run=False, probe=True):
    return status.run(config, _args(verbose=verbose, dry_run=dry_run), runner=cluster.run,
                      kube_factory=cluster.kube, tool_check=lambda commands: None,
                      prober=lambda url, dry: probe)


def test_status_renders_environment_and_components(example_config, capsys):
    config = load(example_config)
    cluster = Cluster("running", workloads=ALL_READY + [ws("deploy/jaeger", 0)])
    rc = _status(config, cluster)
    out = capsys.readouterr().out
    assert "cluster    running" in out and "namespace  dop-local" in out
    for w in ENV_WORKLOADS:
        assert w in out
    assert "jaeger" not in out
    lines = out.splitlines()
    assert any(line.split()[:3] == ["core", "cluster", "ready"] for line in lines)
    assert any(line.split()[:2] == ["cli", "none"] and "n/a" in line for line in lines)
    assert any(line.split()[:3] == ["app", "process", "?"] for line in lines)
    assert "note: app: process state arrives in S0c" in out
    assert "http://api.localtest.me:8080" in out
    assert rc == 3  # app's state is unknown until S0c
    assert "make -C repos/dop-infra status" not in cluster.commands()


def test_status_all_ready_exits_0_without_process_components(write_config, tmp_path, capsys):
    from .conftest import EXAMPLE

    text = EXAMPLE.split("[components.app]")[0] + "[components.bff]" + EXAMPLE.split("[components.bff]")[1]
    config = load(write_config(text))
    assert "app" not in config.components
    assert _status(config, Cluster("running", workloads=ALL_READY)) == 0


def test_status_some_not_ready_exits_3(example_config, capsys):
    workloads = [ws(w, 1) for w in ENV_WORKLOADS] + [ws("deploy/dop-core-serve", 0), ws("deploy/dop-api", 1, 2)]
    assert _status(load(example_config), Cluster("running", workloads=workloads)) == 3
    out = capsys.readouterr().out
    assert "0/1" in out and "1/2" in out
    assert any(line.split()[:4] == ["core", "cluster", "not", "ready"] for line in out.splitlines())


def test_status_missing_workloads_is_down(example_config, capsys):
    assert _status(load(example_config), Cluster("running", workloads=[ws(w, 1) for w in ENV_WORKLOADS])) == 3
    out = capsys.readouterr().out
    assert any(line.split()[:3] == ["core", "cluster", "down"] for line in out.splitlines())
    assert "absent" in out


def test_status_failing_probe_is_not_ready(example_config, capsys):
    assert _status(load(example_config), Cluster("running", workloads=ALL_READY), probe=False) == 3
    out = capsys.readouterr().out
    assert any(line.split()[:4] == ["bff", "cluster", "not", "ready"] for line in out.splitlines())
    assert "no answer" in out


@pytest.mark.parametrize("state", ["absent", "stopped"])
def test_status_cluster_not_running_exits_1(example_config, capsys, state):
    cluster = Cluster(state)
    assert _status(load(example_config), cluster) == 1
    out = capsys.readouterr().out
    assert f"cluster    {state}" in out
    assert ("get",) not in cluster.calls
    assert any(line.split()[:3] == ["core", "cluster", "down"] for line in out.splitlines())


def test_status_kubectl_failing_exits_1(example_config, capsys):
    assert _status(load(example_config), Cluster("running", get_fails=True)) == 1
    assert "kubectl get deploy,sts failed" in capsys.readouterr().out


def test_status_verbose_appends_environment_status(example_config, capsys):
    cluster = Cluster("running", workloads=ALL_READY)
    _status(load(example_config), cluster, verbose=True)
    assert cluster.commands()[-1] == "make -C repos/dop-infra status"
    assert cluster.env_of("make -C repos/dop-infra status") == [KUBECONFIG]
    assert [c for c in cluster.calls if c[0] == "run" and c[1].endswith("status")][0][2] == "stream"


def test_status_dry_run_exits_0_and_parses_nothing(example_config, capsys):
    cluster = Cluster("absent")
    assert _status(load(example_config), cluster, dry_run=True) == 0
    out = capsys.readouterr().out
    assert f"WOULD RUN {EXISTS}" in out and f"WOULD RUN {RUNNING}" in out


def test_status_takes_no_lock(example_config):
    config = load(example_config)
    config.project.state_dir.mkdir(parents=True)
    (config.project.state_dir / "lock").write_text("1\n")
    assert _status(config, Cluster("running", workloads=ALL_READY)) == 3


def test_verbs_through_cli_dispatch(example_config, monkeypatch, capsys):
    """`-v` reaches status through the CLI; the verb modules are what main() calls."""
    from dop import cli

    seen = {}

    def fake_run(config, args):
        seen["verbose"] = args.verbose
        return 0

    monkeypatch.setattr(status, "run", fake_run)
    assert cli.main(["--config", str(example_config), "status", "-v"]) == 0
    assert seen == {"verbose": True}
