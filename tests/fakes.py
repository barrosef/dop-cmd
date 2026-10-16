"""Fakes for the cluster side: a scripted `run` and a `Kube` sharing one ordered call log."""

from __future__ import annotations

import json

from dop.core.process import Result
from dop.kube import WorkloadState

KUBECONFIG = "/tmp/dop-kubeconfig"
EXISTS = "k3d cluster list dop-local"
RUNNING = "k3d cluster list dop-local -o json"
KUBECONFIG_CMD = "k3d kubeconfig write dop-local"


def k3d_json(servers_running: int) -> str:
    return json.dumps([{"name": "dop-local", "serversCount": 1, "serversRunning": servers_running}])


class Cluster:
    """A scripted cluster: `state` is absent | stopped | running; `fail` names commands that fail."""

    def __init__(self, state="running", fail=(), not_ready=(), workloads=None, get_fails=False):
        self.state = state
        self.fail = set(fail)
        self.not_ready = set(not_ready)
        self.workloads = workloads or []
        self.get_fails = get_fails
        self.calls: list[tuple] = []

    def run(self, cmd, *, cwd, env=None, mode="stream", log_file=None, dry_run=False):
        self.calls.append(("run", cmd, mode, (env or {}).get("KUBECONFIG")))
        if dry_run:
            print(f"WOULD RUN {cmd}")
            return Result(rc=0)
        if cmd in self.fail:
            return Result(rc=2, stdout="boom")
        if cmd == EXISTS:
            return Result(rc=1 if self.state == "absent" else 0)
        if cmd == RUNNING:
            return Result(rc=0, stdout=k3d_json(1 if self.state == "running" else 0))
        if cmd == KUBECONFIG_CMD:
            if self.state == "absent":
                return Result(rc=1, stdout="no cluster")
            return Result(rc=0, stdout=KUBECONFIG + "\n")
        if cmd.endswith("cluster-up"):
            self.state = "running"
        if cmd.startswith("k3d cluster start"):
            self.state = "running"
        if cmd.endswith("cluster-stop"):
            self.state = "stopped"
        return Result(rc=0)

    def kube(self, kubeconfig_path, namespace, **kwargs):
        cluster = self
        dry_run = kwargs.get("dry_run", False)
        cluster.calls.append(("kube", kubeconfig_path, namespace))

        class FakeKube:
            def rollout_wait(self, workload, timeout):
                cluster.calls.append(("wait", workload))
                if dry_run:
                    print(f"WOULD WAIT rollout {workload}")
                    return True
                return workload not in cluster.not_ready

            def get_workloads(self):
                cluster.calls.append(("get",))
                if cluster.get_fails:
                    from dop.core.errors import ProcessError

                    raise ProcessError("kubectl get deploy,sts failed (rc 1)")
                return list(cluster.workloads)

        return FakeKube()

    # views over the log
    def commands(self):
        return [c[1] for c in self.calls if c[0] == "run"]

    def waits(self):
        return [c[1] for c in self.calls if c[0] == "wait"]

    def env_of(self, cmd):
        return [c[3] for c in self.calls if c[0] == "run" and c[1] == cmd]


def ws(ref: str, ready: int, desired: int = 1) -> WorkloadState:
    kind, name = ref.split("/", 1)
    return WorkloadState(kind=kind, name=name, ready=ready, desired=desired)
