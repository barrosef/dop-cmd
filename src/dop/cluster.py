"""The environment's cluster, driven only through the commands `dop.toml` declares.

Every `make` and `kubectl` the driver runs carries ``KUBECONFIG=<path>``, the
path the environment's ``kubeconfig`` command prints, so `dop-infra`'s context
guard passes without touching the machine's current context. The k3d commands
(``cluster_exists``, ``cluster_running``, ``cluster_up``, ``cluster_start``)
do not need it, and the path is resolved lazily: a cluster that does not exist
yet has no kubeconfig to write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, Literal

from .config import Environment
from .core import process
from .core.errors import ProcessError
from .kube import Kube

State = Literal["absent", "stopped", "running"]


class ClusterDriver:
    def __init__(
        self,
        env: Environment,
        *,
        runner: Callable[..., process.Result] | None = None,
        kube_factory: Callable[..., Kube] = Kube,
        dry_run: bool = False,
        cwd: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.env = env
        self.runner = runner or process.run
        self.kube_factory = kube_factory
        self.dry_run = dry_run
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.log_dir = log_dir
        self._kubeconfig: str | None = None
        self._kube: Kube | None = None

    # ------------------------------------------------------------------ plumbing

    def _run(self, cmd: str, mode: str = "stream", env: dict[str, str] | None = None) -> process.Result:
        return self.runner(cmd, cwd=self.cwd, env=env, mode=mode, dry_run=self.dry_run)

    def _check(self, what: str, cmd: str, env: dict[str, str] | None = None) -> None:
        result = self._run(cmd, "stream", env)
        if result.rc != 0:
            raise ProcessError(f"{what} failed (rc {result.rc}): {cmd}")

    def kubeconfig_path(self) -> str:
        """Run the ``kubeconfig`` command once (capture) and return the path it prints."""
        if self._kubeconfig is None:
            result = self._run(self.env.kubeconfig, "capture")
            if result.rc != 0:
                detail = f": {result.stdout.strip()}" if result.stdout and result.stdout.strip() else ""
                raise ProcessError(f"kubeconfig command failed (rc {result.rc}): {self.env.kubeconfig}{detail}")
            path = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
            if not path and not self.dry_run:
                raise ProcessError(f"kubeconfig command printed no path: {self.env.kubeconfig}")
            self._kubeconfig = path
        return self._kubeconfig

    def _kube_env(self, *, required: bool = True) -> dict[str, str] | None:
        """``{"KUBECONFIG": path}``; when not required, None if the path cannot be resolved."""
        try:
            return {"KUBECONFIG": self.kubeconfig_path()}
        except ProcessError:
            if required:
                raise
            return None

    def kube(self) -> Kube:
        if self._kube is None:
            self._kube = self.kube_factory(
                self.kubeconfig_path(),
                self.env.namespace,
                log_dir=self.log_dir,
                dry_run=self.dry_run,
                cwd=self.cwd,
            )
        return self._kube

    # ------------------------------------------------------------------ cluster

    def state(self) -> State:
        """``absent`` when ``cluster_exists`` fails; else ``running`` when any server runs, else ``stopped``.

        In dry-run the commands are printed and the cluster is assumed running.
        """
        if self.env.cluster_exists:
            result = self._run(self.env.cluster_exists, "capture")
            if not self.dry_run and result.rc != 0:
                return "absent"
        if not self.env.cluster_running:
            return "running"
        result = self._run(self.env.cluster_running, "capture")
        if self.dry_run:
            return "running"
        if result.rc != 0:
            raise ProcessError(f"cluster_running failed (rc {result.rc}): {self.env.cluster_running}")
        return "running" if _servers_running(result.stdout) else "stopped"

    def ensure_running(self) -> State:
        """Bring the cluster to running; return the state it was found in."""
        found = self.state()
        if found == "absent":
            if not self.env.cluster_up:
                raise ProcessError("the cluster does not exist and the environment declares no cluster_up")
            self._check("cluster_up", self.env.cluster_up)
            for cmd in self.env.prepare:
                self._check("prepare", cmd, self._kube_env(required=False))
        elif found == "stopped":
            if not self.env.cluster_start:
                raise ProcessError("the cluster is stopped and the environment declares no cluster_start")
            self._check("cluster_start", self.env.cluster_start)
        return found

    def stop(self) -> None:
        if not self.env.cluster_stop:
            raise ProcessError("the environment declares no cluster_stop")
        self._check("cluster_stop", self.env.cluster_stop, self._kube_env(required=False))

    # ------------------------------------------------------------------ environment

    def env_up(self) -> None:
        if not self.env.up:
            raise ProcessError("the environment declares no up")
        self._check("up", self.env.up, self._kube_env())

    def env_down(self) -> None:
        if not self.env.down:
            raise ProcessError("the environment declares no down")
        self._check("down", self.env.down, self._kube_env())

    def env_status(self) -> int:
        """The environment's ``status`` command, streamed verbatim; its return code."""
        if not self.env.status:
            print("(the environment declares no status command)", file=sys.stderr)
            return 0
        return self._run(self.env.status, "stream", self._kube_env()).rc

    def wait_workloads(self, workloads: list[str]) -> None:
        """``rollout_wait`` each workload in order; the first that is not ready raises ``ProcessError``."""
        kube = self.kube() if workloads else None
        for workload in workloads:
            if not kube.rollout_wait(workload, self.env.wait_timeout):
                raise ProcessError(f"{workload} not ready within {self.env.wait_timeout:g}s")

    def wait_environment(self) -> None:
        self.wait_workloads(self.env.workloads)


def _servers_running(text: str) -> bool:
    """k3d's ``cluster list -o json``: a list of clusters (or one) with ``serversRunning``."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProcessError(f"cluster_running: output is not JSON ({exc.msg})") from None
    clusters = document if isinstance(document, list) else [document]
    for cluster in clusters:
        if isinstance(cluster, dict):
            try:
                if int(cluster.get("serversRunning") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def driver_for(
    config,
    *,
    runner: Callable[..., process.Result] | None = None,
    kube_factory: Callable[..., Kube] = Kube,
    dry_run: bool = False,
) -> ClusterDriver:
    """The default environment's driver, running from the project root."""
    return ClusterDriver(
        config.environment,
        runner=runner,
        kube_factory=kube_factory,
        dry_run=dry_run,
        cwd=config.root,
        log_dir=config.project.state_dir,
    )


def environment_tools(env: Environment, *fields: str) -> list[str]:
    """The declared commands named by ``fields`` (``prepare`` expands), plus ``kubectl``."""
    commands: list[str] = []
    for name in fields:
        value = getattr(env, name)
        if isinstance(value, list):
            commands.extend(value)
        elif value:
            commands.append(value)
    return commands + ["kubectl"]
