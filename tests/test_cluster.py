from __future__ import annotations

import pytest

from dop.cluster import ClusterDriver, driver_for
from dop.config import load
from dop.core.errors import ProcessError

from .fakes import EXISTS, KUBECONFIG, KUBECONFIG_CMD, RUNNING, Cluster

ENV_WORKLOADS = ["sts/postgres", "sts/nats", "sts/firebase", "deploy/secretmanager"]


def _driver(config, cluster, dry_run=False) -> ClusterDriver:
    return driver_for(config, runner=cluster.run, kube_factory=cluster.kube, dry_run=dry_run)


@pytest.mark.parametrize("state", ["absent", "stopped", "running"])
def test_state(example_config, state):
    cluster = Cluster(state)
    assert _driver(load(example_config), cluster).state() == state
    expected = [EXISTS] if state == "absent" else [EXISTS, RUNNING]
    assert cluster.commands() == expected
    assert all(c[2] == "capture" for c in cluster.calls)


def test_state_dry_run_assumes_running_and_parses_nothing(example_config, capsys):
    cluster = Cluster("absent")
    assert _driver(load(example_config), cluster, dry_run=True).state() == "running"
    assert capsys.readouterr().out.splitlines() == [f"WOULD RUN {EXISTS}", f"WOULD RUN {RUNNING}"]


def test_state_bad_json_raises(example_config):
    cluster = Cluster("running")
    original = cluster.run

    def run(cmd, **kw):
        if cmd == RUNNING:
            from dop.core.process import Result

            return Result(rc=0, stdout="not json")
        return original(cmd, **kw)

    cluster.run = run
    with pytest.raises(ProcessError, match="not JSON"):
        _driver(load(example_config), cluster).state()


def test_ensure_running_absent_creates_then_prepares_in_order(example_config):
    cluster = Cluster("absent")
    assert _driver(load(example_config), cluster).ensure_running() == "absent"
    assert cluster.commands() == [
        EXISTS,
        "make -C repos/dop-infra cluster-up",
        KUBECONFIG_CMD,
        "make -C repos/dop-infra images",
        "make -C repos/dop-infra image-runner",
    ]
    streamed = [c for c in cluster.calls if c[0] == "run" and c[2] == "stream"]
    assert [c[1] for c in streamed][0].endswith("cluster-up")
    assert cluster.env_of("make -C repos/dop-infra images") == [KUBECONFIG]


def test_ensure_running_stopped_starts(example_config):
    cluster = Cluster("stopped")
    assert _driver(load(example_config), cluster).ensure_running() == "stopped"
    assert cluster.commands() == [EXISTS, RUNNING, "k3d cluster start dop-local"]


def test_ensure_running_running_does_nothing(example_config):
    cluster = Cluster("running")
    _driver(load(example_config), cluster).ensure_running()
    assert cluster.commands() == [EXISTS, RUNNING]


def test_ensure_running_failing_cluster_up_raises(example_config):
    cluster = Cluster("absent", fail={"make -C repos/dop-infra cluster-up"})
    with pytest.raises(ProcessError, match="cluster_up failed"):
        _driver(load(example_config), cluster).ensure_running()
    assert "make -C repos/dop-infra images" not in cluster.commands()


def test_make_calls_carry_kubeconfig_resolved_once(example_config):
    cluster = Cluster("running")
    driver = _driver(load(example_config), cluster)
    driver.env_up()
    driver.env_down()
    driver.env_status()
    driver.stop()
    for cmd in ("up", "down", "status", "cluster-stop"):
        assert cluster.env_of(f"make -C repos/dop-infra {cmd}") == [KUBECONFIG]
    assert cluster.commands().count(KUBECONFIG_CMD) == 1
    assert [c for c in cluster.calls if c[0] == "run" and c[1] == KUBECONFIG_CMD][0][2] == "capture"


def test_env_up_without_kubeconfig_fails(example_config):
    cluster = Cluster("running", fail={KUBECONFIG_CMD})
    with pytest.raises(ProcessError, match="kubeconfig command failed"):
        _driver(load(example_config), cluster).env_up()
    assert "make -C repos/dop-infra up" not in cluster.commands()


def test_env_up_failure_raises(example_config):
    cluster = Cluster("running", fail={"make -C repos/dop-infra up"})
    with pytest.raises(ProcessError, match="up failed"):
        _driver(load(example_config), cluster).env_up()


def test_wait_environment_waits_declared_workloads_in_order(example_config):
    cluster = Cluster("running")
    _driver(load(example_config), cluster).wait_environment()
    assert cluster.waits() == ENV_WORKLOADS
    assert ("kube", KUBECONFIG, "dop-local") in cluster.calls


def test_wait_environment_stops_at_first_not_ready(example_config):
    cluster = Cluster("running", not_ready={"sts/nats"})
    with pytest.raises(ProcessError, match="sts/nats not ready within 240s"):
        _driver(load(example_config), cluster).wait_environment()
    assert cluster.waits() == ["sts/postgres", "sts/nats"]
