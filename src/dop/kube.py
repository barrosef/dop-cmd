"""kubectl, always with KUBECONFIG set and the namespace explicit.

`dop` never runs `kubectl config use-context`: every call carries the
kubeconfig the environment's `kubeconfig` command printed.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from .core.errors import ProcessError
from .core.process import kill_group, run

PORT_FORWARD_TIMEOUT = 30.0


# `kubectl` resource kinds as `dop.toml` may spell them, folded to one short form.
_KIND_ALIASES = {
    "deploy": "deploy",
    "deployment": "deploy",
    "deployments": "deploy",
    "sts": "sts",
    "statefulset": "sts",
    "statefulsets": "sts",
}
_KIND_OF_OBJECT = {"Deployment": "deploy", "StatefulSet": "sts"}


def workload_ref(workload: str) -> str:
    """Fold ``deployment/x`` / ``Deployment/x`` / ``deploy/x`` to ``deploy/x`` (likewise ``sts``)."""
    kind, sep, name = workload.partition("/")
    if not sep:
        return workload
    return f"{_KIND_ALIASES.get(kind.lower(), kind.lower())}/{name}"


@dataclass(frozen=True)
class WorkloadState:
    kind: str  # "deploy" or "sts"
    name: str
    ready: int
    desired: int

    @property
    def ref(self) -> str:
        return f"{self.kind}/{self.name}"

    @property
    def is_ready(self) -> bool:
        return self.desired > 0 and self.ready >= self.desired


def parse_workloads(text: str) -> list[WorkloadState]:
    """Read ``kubectl get deploy,sts -o json``; a missing ``readyReplicas`` is 0."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProcessError(f"kubectl get: output is not JSON ({exc.msg})") from None
    items = document.get("items", []) if isinstance(document, dict) else []
    states = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_kind = item.get("kind", "")
        kind = _KIND_OF_OBJECT.get(raw_kind, raw_kind.lower())
        name = (item.get("metadata") or {}).get("name", "")
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        desired = spec.get("replicas")
        states.append(
            WorkloadState(
                kind=kind,
                name=name,
                ready=int(status.get("readyReplicas") or 0),
                desired=int(1 if desired is None else desired),
            )
        )
    return states


def _local_ports(spec: str) -> list[int]:
    """Local ports of a forward spec like ``svc/postgres 5432:5432 6000``."""
    words = spec.split()
    if len(words) < 2:
        raise ProcessError(f"port-forward '{spec}': expected '<resource> <local:remote>...'")
    ports = []
    for mapping in words[1:]:
        local = mapping.split(":", 1)[0]
        if not local.isdigit():
            raise ProcessError(f"port-forward '{spec}': cannot read a local port from '{mapping}'")
        ports.append(int(local))
    return ports


def _port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
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
    except PermissionError:
        return True
    return True


class Kube:
    def __init__(
        self,
        kubeconfig_path: str,
        namespace: str,
        *,
        log_dir: Path | None = None,
        dry_run: bool = False,
        cwd: Path | None = None,
        forward_timeout: float = PORT_FORWARD_TIMEOUT,
    ) -> None:
        self.kubeconfig_path = kubeconfig_path
        self.namespace = namespace
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.dry_run = dry_run
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.forward_timeout = forward_timeout

    @property
    def env(self) -> dict[str, str]:
        return {"KUBECONFIG": self.kubeconfig_path}

    def rollout_wait(self, workload: str, timeout: float) -> bool:
        """``kubectl rollout status`` streamed to the terminal; True when it reports ready in time.

        In dry-run nothing runs: ``WOULD WAIT rollout <workload>`` and True.
        """
        if self.dry_run:
            print(f"WOULD WAIT rollout {workload}", flush=True)
            return True
        cmd = f"kubectl -n {self.namespace} rollout status {workload} --timeout={timeout:g}s"
        return run(cmd, cwd=self.cwd, env=self.env, mode="stream").rc == 0

    def get_workloads(self) -> list[WorkloadState]:
        """Every Deployment and StatefulSet in the namespace, with ready/desired replicas.

        In dry-run the command is printed, nothing is parsed and the list is empty.
        A failing kubectl raises ``ProcessError``.
        """
        cmd = f"kubectl -n {self.namespace} get deploy,sts -o json"
        if self.dry_run:
            run(cmd, cwd=self.cwd, env=self.env, mode="capture", dry_run=True)
            return []
        result = run(cmd, cwd=self.cwd, env=self.env, mode="capture")
        if result.rc != 0:
            detail = f": {result.stdout.strip()}" if result.stdout.strip() else ""
            raise ProcessError(f"kubectl get deploy,sts failed (rc {result.rc}){detail}")
        return parse_workloads(result.stdout)

    def _log_file(self, spec: str) -> Path:
        if self.log_dir is None:
            return Path(os.devnull)
        slug = "".join(c if c.isalnum() else "-" for c in spec).strip("-")
        return self.log_dir / f"port-forward-{slug}.log"

    def port_forward(self, spec: str) -> int | None:
        """Open ``kubectl port-forward -n <ns> <spec>`` detached; return its pid once every local port answers.

        In dry-run nothing runs and ``None`` is returned. A forward that dies or
        does not answer within the timeout is closed and raises ``ProcessError``.
        """
        ports = _local_ports(spec)
        cmd = f"kubectl port-forward -n {self.namespace} {spec}"
        if self.dry_run:
            run(cmd, cwd=self.cwd, env=self.env, mode="detach", log_file=self._log_file(spec), dry_run=True)
            print(f"WOULD WAIT port-forward {spec}", flush=True)
            return None
        result = run(cmd, cwd=self.cwd, env=self.env, mode="detach", log_file=self._log_file(spec))
        pid = result.pid
        deadline = time.monotonic() + self.forward_timeout
        while time.monotonic() < deadline:
            alive = _pid_alive(pid)
            if alive and all(_port_answers(port) for port in ports):
                return pid
            if not alive:
                kill_group(pid, grace=1)
                raise ProcessError(f"port-forward '{spec}' exited before its port answered")
            time.sleep(0.2)
        kill_group(pid)
        raise ProcessError(f"port-forward '{spec}' did not answer within {self.forward_timeout:g}s")
