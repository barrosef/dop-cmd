from __future__ import annotations

import argparse

import pytest

from dop.config import ConfigError, load
from dop.core.errors import ProcessError
from dop.core.process import Result
from dop.verbs import test as verb


class Recorder:
    """A fake `run` and a fake `Kube` sharing one ordered call log."""

    def __init__(self, fail_on: str | None = None, forward_fails: str | None = None, kubeconfig_rc: int = 0):
        self.calls: list[tuple] = []
        self.fail_on = fail_on
        self.forward_fails = forward_fails
        self.kubeconfig_rc = kubeconfig_rc
        self.next_pid = 1000

    def run(self, cmd, *, cwd, env=None, mode="stream", log_file=None, dry_run=False):
        self.calls.append(("run", cmd, mode, str(cwd)))
        if mode == "capture":
            return Result(rc=self.kubeconfig_rc, stdout="/tmp/kubeconfig\n" if self.kubeconfig_rc == 0 else "boom")
        return Result(rc=1 if cmd == self.fail_on else 0)

    def kube(self, kubeconfig_path, namespace, **kwargs):
        self.calls.append(("kube", kubeconfig_path, namespace))
        recorder = self

        class FakeKube:
            def port_forward(self, spec):
                if spec == recorder.forward_fails:
                    raise ProcessError(f"port-forward '{spec}' did not answer")
                recorder.next_pid += 1
                recorder.calls.append(("forward", spec, recorder.next_pid))
                return recorder.next_pid

        return FakeKube()

    def kill(self, pid):
        self.calls.append(("close", pid))

    def tools(self, commands):
        self.calls.append(("tools", tuple(commands)))


def _args(component, layer="unit", dry_run=False):
    return argparse.Namespace(component=component, layer=layer, dry_run=dry_run, verbose=False, config=None)


def _invoke(config, recorder, *args, **kwargs):
    return verb.run(
        config, _args(*args, **kwargs),
        runner=recorder.run, kube_factory=recorder.kube, killer=recorder.kill, tool_check=recorder.tools,
    )


def _steps(recorder):
    return [c for c in recorder.calls if c[0] != "tools"]


def test_unit_layer_runs_setup_then_commands_in_repo(example_config):
    config = load(example_config)
    r = Recorder()
    assert _invoke(config, r, "app") == 0
    repo = str(config.components["app"].repo)
    assert _steps(r) == [
        ("run", "pnpm install --frozen-lockfile", "stream", repo),
        ("run", "pnpm typecheck", "stream", repo),
        ("run", "pnpm -C artifacts/dop test", "stream", repo),
    ]
    assert r.calls[0] == ("tools", ("pnpm install --frozen-lockfile", "pnpm typecheck", "pnpm -C artifacts/dop test"))


def test_layer_with_forwards_opens_runs_and_closes(example_config):
    config = load(example_config)
    r = Recorder()
    assert _invoke(config, r, "core", "integration") == 0
    repo = str(config.components["core"].repo)
    assert _steps(r) == [
        ("run", "k3d kubeconfig write dop-local", "capture", str(config.root)),
        ("kube", "/tmp/kubeconfig", "dop-local"),
        ("forward", "svc/postgres 5432:5432", 1001),
        ("forward", "svc/nats 4222:4222", 1002),
        ("run", "make test-integration", "stream", repo),
        ("close", 1001),
        ("close", 1002),
    ]
    assert any("kubectl" in c[1] for c in r.calls if c[0] == "tools")


def test_first_failure_stops_and_forwards_still_close(example_config):
    config = load(example_config)
    r = Recorder(fail_on="make test-contract-integration")
    assert _invoke(config, r, "core", "contract") == 1
    names = [c[1] for c in _steps(r) if c[0] == "run"]
    assert "make proto-breaking" not in names
    assert _steps(r)[-1] == ("close", 1001)


def test_failing_forward_closes_the_ones_already_open(example_config):
    config = load(example_config)
    r = Recorder(forward_fails="svc/nats 4222:4222")
    assert _invoke(config, r, "core", "integration") == 1
    assert not any(c[0] == "run" and c[1] == "make test-integration" for c in r.calls)
    assert _steps(r)[-1] == ("close", 1001)


def test_failing_setup_stops_before_anything_else(example_config):
    config = load(example_config)
    r = Recorder(fail_on="uv sync")
    assert _invoke(config, r, "bff") == 1
    assert _steps(r) == [("run", "uv sync", "stream", str(config.components["bff"].repo))]


def test_failing_kubeconfig_fails_without_forwards(example_config):
    config = load(example_config)
    r = Recorder(kubeconfig_rc=1)
    assert _invoke(config, r, "core", "integration") == 1
    assert [c[0] for c in _steps(r)] == ["run"]


def test_undeclared_layer_is_exit_2(example_config, capsys):
    config = load(example_config)
    r = Recorder()
    assert _invoke(config, r, "cli", "contract") == 2
    assert r.calls == []
    assert "declares no 'contract' test layer" in capsys.readouterr().err


def test_unknown_component_is_a_config_error(example_config):
    with pytest.raises(ConfigError):
        _invoke(load(example_config), Recorder(), "ghost")


def test_dry_run_prints_would_run_and_would_wait(example_config, capsys, monkeypatch):
    config = load(example_config)

    def no_tool_check(commands):  # dry-run must not look for tools
        raise AssertionError("tool check in dry-run")

    rc = verb.run(config, _args("core", "contract", dry_run=True), tool_check=no_tool_check,
                  killer=lambda pid: (_ for _ in ()).throw(AssertionError("nothing to close")))
    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "WOULD RUN k3d kubeconfig write dop-local",
        "WOULD RUN kubectl port-forward -n dop-local svc/nats 4222:4222",
        "WOULD WAIT port-forward svc/nats 4222:4222",
        "WOULD RUN make test-contract-integration",
        "WOULD RUN make proto-breaking",
        "dop test: core contract dry run, nothing ran",
    ]


def test_real_commands_through_sh(write_config, tmp_path):
    (tmp_path / "w").mkdir()
    path = write_config('''
[project]
name = "x"
default_environment = "local"
[environments.local]
kubeconfig = "echo /k"
namespace = "ns"
up = "true"
down = "true"
cluster_exists = "true"
cluster_running = "true"
cluster_up = "true"
cluster_start = "true"
cluster_stop = "true"
[components.w]
repo = "w"
kind = "none"
setup = "echo setup >> order.txt"
[components.w.test]
unit = ["echo one >> order.txt", "sh -c 'exit 5'", "echo never >> order.txt"]
''')
    rc = verb.run(load(path), _args("w"))
    assert rc == 1
    assert (tmp_path / "w" / "order.txt").read_text().split() == ["setup", "one"]
