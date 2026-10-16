"""`dop logs` for the three kinds of component; nothing real follows anything."""

from __future__ import annotations

import os
import argparse
import dataclasses

import pytest

from dop.config import ConfigError, load
from dop.core import process
from dop.processes import ProcessManager
from dop.verbs import logs

from .fakes import KUBECONFIG, KUBECONFIG_CMD, Cluster

BFF_LOGS = "make -C repos/dop-infra logs C=dop-api"


def _args(component, dry_run=False):
    return argparse.Namespace(component=component, dry_run=dry_run, verbose=False, config=None)


def _logs(config, cluster, component, dry_run=False, runner=None, pm=None):
    return logs.run(config, _args(component, dry_run), runner=runner or cluster.run, kube_factory=cluster.kube,
                    tool_check=lambda commands: None, processes=pm)


def test_cluster_component_streams_its_logs_command_with_kubeconfig_from_the_root(example_config):
    config = load(example_config)
    cluster = Cluster("running")
    seen = []
    original = cluster.run

    def run(cmd, **kw):
        seen.append((cmd, kw.get("cwd"), kw.get("mode"), (kw.get("env") or {}).get("KUBECONFIG")))
        return original(cmd, **kw)

    assert _logs(config, cluster, "bff", runner=run) == 0
    assert (BFF_LOGS, config.root, "stream", KUBECONFIG) in seen
    assert any(cmd == KUBECONFIG_CMD for cmd, *_ in seen)


def test_cluster_logs_nonzero_rc_is_exit_1(example_config):
    cluster = Cluster("running", fail={BFF_LOGS})
    assert _logs(load(example_config), cluster, "bff") == 1


def test_cluster_logs_ctrl_c_is_exit_0(example_config, capsys):
    cluster = Cluster("running")
    original = cluster.run

    def run(cmd, **kw):
        if cmd == BFF_LOGS:
            raise KeyboardInterrupt
        return original(cmd, **kw)

    assert _logs(load(example_config), cluster, "bff", runner=run) == 0


def test_cluster_without_a_logs_command_is_exit_3(example_config, capsys):
    config = load(example_config)
    assert config.components["core"].logs is None
    assert _logs(config, Cluster("running"), "core") == 3
    assert "declares no logs command" in capsys.readouterr().out


def test_kubeconfig_failure_is_exit_1(example_config, capsys):
    cluster = Cluster("absent")
    assert _logs(load(example_config), cluster, "bff") == 1
    assert "kubeconfig command failed" in capsys.readouterr().out


def test_cluster_dry_run(example_config, capsys):
    cluster = Cluster("absent")
    assert _logs(load(example_config), cluster, "bff", dry_run=True) == 0
    assert capsys.readouterr().out.splitlines() == [f"WOULD RUN {KUBECONFIG_CMD}", f"WOULD RUN {BFF_LOGS}"]


def test_process_tails_its_log(example_config):
    config = load(example_config)
    pm = ProcessManager(config.project.state_dir)
    pm.state_dir.mkdir(parents=True)
    pm.log_path(config.components["app"]).write_text("line\n")
    pm.pid_path(config.components["app"]).write_text(f"{os.getpid()}\n")  # alive: this very test
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw.get("mode"), kw.get("cwd")))
        return process.Result(rc=0)

    assert _logs(config, Cluster(), "app", runner=run, pm=pm) == 0
    assert seen == [(f"tail -f {pm.log_path(config.components['app'])}", "stream", config.root)]


def test_process_tail_really_follows_and_ctrl_c_is_exit_0(example_config):
    config = load(example_config)
    pm = ProcessManager(config.project.state_dir)
    pm.state_dir.mkdir(parents=True)
    pm.log_path(config.components["app"]).write_text("line\n")
    pm.pid_path(config.components["app"]).write_text(f"{os.getpid()}\n")  # alive: this very test

    def run(cmd, **kw):
        assert cmd.startswith("tail -f ")
        raise KeyboardInterrupt

    assert _logs(config, Cluster(), "app", runner=run, pm=pm) == 0


def test_process_never_started_is_exit_3(example_config, capsys):
    config = load(example_config)
    pm = ProcessManager(config.project.state_dir)
    cluster = Cluster()
    assert _logs(config, cluster, "app", pm=pm) == 3
    assert "app was never started" in capsys.readouterr().out
    assert cluster.calls == []


def test_process_dry_run(example_config, capsys):
    config = load(example_config)
    pm = ProcessManager(config.project.state_dir)
    assert _logs(config, Cluster(), "app", dry_run=True, pm=pm) == 0
    assert capsys.readouterr().out.strip() == f"WOULD RUN tail -f {pm.log_path(config.components['app'])}"


def test_none_has_nothing_to_follow(example_config, capsys):
    cluster = Cluster()
    assert _logs(load(example_config), cluster, "cli") == 3
    assert "cli has no runtime; nothing to follow" in capsys.readouterr().out
    assert cluster.calls == []


def test_unknown_component_is_a_config_error(example_config):
    with pytest.raises(ConfigError, match="unknown component 'ghost'"):
        _logs(load(example_config), Cluster(), "ghost")
