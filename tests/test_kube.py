from __future__ import annotations

import json

import pytest

from dop import kube as kube_module
from dop.core.errors import ProcessError
from dop.core.process import Result, run
from dop.kube import Kube, WorkloadState, parse_workloads, workload_ref


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


def test_rollout_wait_streams_with_kubeconfig(monkeypatch):
    calls = []
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Result(rc=0))
    assert Kube("/tmp/kc", "dop-local").rollout_wait("sts/postgres", 240.0) is True
    cmd, kwargs = calls[0]
    assert cmd == "kubectl -n dop-local rollout status sts/postgres --timeout=240s"
    assert kwargs["env"] == {"KUBECONFIG": "/tmp/kc"} and kwargs["mode"] == "stream"


def test_rollout_wait_failure_is_false(monkeypatch):
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: Result(rc=1))
    assert Kube("/k", "ns").rollout_wait("deploy/x", 1.5) is False


def test_rollout_wait_dry_run_runs_nothing(monkeypatch, capsys):
    monkeypatch.setattr(kube_module, "run", lambda *a, **k: pytest.fail("ran in dry-run"))
    assert Kube("/k", "ns", dry_run=True).rollout_wait("deploy/x", 5) is True
    assert capsys.readouterr().out == "WOULD WAIT rollout deploy/x\n"


GET_JSON = json.dumps({
    "items": [
        {"kind": "Deployment", "metadata": {"name": "dop-api"}, "spec": {"replicas": 2}, "status": {"readyReplicas": 1}},
        {"kind": "Deployment", "metadata": {"name": "dop-core-serve"}, "spec": {"replicas": 1}, "status": {}},
        {"kind": "StatefulSet", "metadata": {"name": "postgres"}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}},
    ]
})


def test_get_workloads_parses_deploy_and_sts(monkeypatch):
    calls = []
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: calls.append((cmd, kw)) or Result(rc=0, stdout=GET_JSON))
    states = Kube("/tmp/kc", "dop-local").get_workloads()
    assert calls[0][0] == "kubectl -n dop-local get deploy,sts -o json"
    assert calls[0][1]["mode"] == "capture" and calls[0][1]["env"] == {"KUBECONFIG": "/tmp/kc"}
    assert states == [
        WorkloadState("deploy", "dop-api", 1, 2),
        WorkloadState("deploy", "dop-core-serve", 0, 1),
        WorkloadState("sts", "postgres", 1, 1),
    ]
    assert [s.is_ready for s in states] == [False, False, True]
    assert states[2].ref == "sts/postgres"


def test_get_workloads_failure_raises(monkeypatch):
    monkeypatch.setattr(kube_module, "run", lambda cmd, **kw: Result(rc=1, stdout="connection refused"))
    with pytest.raises(ProcessError, match="connection refused"):
        Kube("/k", "ns").get_workloads()


def test_get_workloads_bad_json_raises():
    with pytest.raises(ProcessError, match="not JSON"):
        parse_workloads("nope")


def test_get_workloads_dry_run_parses_nothing(monkeypatch, capsys):
    monkeypatch.setattr(kube_module, "run", run)
    assert Kube("/k", "ns", dry_run=True).get_workloads() == []
    assert capsys.readouterr().out == "WOULD RUN kubectl -n ns get deploy,sts -o json\n"


@pytest.mark.parametrize("given, folded", [
    ("deploy/x", "deploy/x"), ("deployment/x", "deploy/x"), ("Deployment/x", "deploy/x"),
    ("sts/y", "sts/y"), ("statefulset/y", "sts/y"),
])
def test_workload_ref(given, folded):
    assert workload_ref(given) == folded
