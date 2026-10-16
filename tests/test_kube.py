from __future__ import annotations

import pytest

from dop import kube as kube_module
from dop.core.errors import ProcessError
from dop.core.process import Result
from dop.kube import Kube


def test_port_forward_dry_run(capsys, tmp_path):
    k = Kube("/k", "dop-local", log_dir=tmp_path, dry_run=True)
    assert k.port_forward("svc/postgres 5432:5432") is None
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "WOULD RUN kubectl port-forward -n dop-local svc/postgres 5432:5432",
        "WOULD WAIT port-forward svc/postgres 5432:5432",
    ]


def test_port_forward_runs_detached_with_kubeconfig_and_waits(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result(rc=0, pid=4242)

    monkeypatch.setattr(kube_module, "run", fake_run)
    monkeypatch.setattr(kube_module, "_pid_alive", lambda pid: True)
    answered = iter([False, True])
    monkeypatch.setattr(kube_module, "_port_answers", lambda port: next(answered))
    monkeypatch.setattr(kube_module.time, "sleep", lambda s: None)

    k = Kube("/tmp/kc", "ns", log_dir=tmp_path)
    assert k.port_forward("svc/nats 4222:4222") == 4242
    cmd, kwargs = calls[0]
    assert cmd == "kubectl port-forward -n ns svc/nats 4222:4222"
    assert kwargs["mode"] == "detach"
    assert kwargs["env"] == {"KUBECONFIG": "/tmp/kc"}
    assert kwargs["log_file"].parent == tmp_path


def test_port_forward_dead_process_fails(monkeypatch, tmp_path):
    killed = []
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: Result(rc=0, pid=99))
    monkeypatch.setattr(kube_module, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(kube_module, "kill_group", lambda pid, grace=10: killed.append(pid))
    with pytest.raises(ProcessError, match="exited"):
        Kube("/k", "ns", log_dir=tmp_path).port_forward("svc/a 1234:1")
    assert killed == [99]


def test_port_forward_timeout_fails_and_closes(monkeypatch, tmp_path):
    killed = []
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: Result(rc=0, pid=77))
    monkeypatch.setattr(kube_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(kube_module, "_port_answers", lambda port: False)
    monkeypatch.setattr(kube_module, "kill_group", lambda pid, grace=10: killed.append(pid))
    monkeypatch.setattr(kube_module.time, "sleep", lambda s: None)
    with pytest.raises(ProcessError, match="did not answer"):
        Kube("/k", "ns", log_dir=tmp_path, forward_timeout=0.01).port_forward("svc/a 1234:1")
    assert killed == [77]


@pytest.mark.parametrize("spec", ["svc/a", "svc/a abc:1"])
def test_port_forward_bad_spec(spec):
    with pytest.raises(ProcessError):
        Kube("/k", "ns", dry_run=True).port_forward(spec)


def test_cluster_side_is_a_later_slice():
    k = Kube("/k", "ns")
    with pytest.raises(NotImplementedError):
        k.rollout_wait("deploy/x", 1)
    with pytest.raises(NotImplementedError):
        k.get_workloads()
