"""`dop down` — stops the app processes and the local cluster; data is kept.

Every `process` component `dop` owns is stopped first (SIGTERM to the group,
SIGKILL after 10 s, pid file removed), then the
environment's `cluster_stop`. The next `dop up` finds the cluster stopped and
runs `cluster_start`. Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
from typing import Callable

from ..cluster import ClusterDriver, driver_for
from ..config import Config
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube
from ..processes import LockHeld, ProcessManager


def stop_owned_processes(
    config: Config, *, processes: ProcessManager, dry_run: bool, verb: str = "down", grace: float = 10
) -> list[str]:
    """Stop every `process` component `dop` owns (SIGTERM the group, SIGKILL after ``grace``); one line each.

    Returns the names stopped (in dry-run, the names that would be).
    """
    owned = set(processes.owned())
    stopped: list[str] = []
    for component in config.components.values():
        if component.kind != "process" or component.name not in owned:
            continue
        if dry_run:
            if processes.alive(component, cleanup=False) is not None:
                print(f"WOULD STOP {component.name}", flush=True)
                stopped.append(component.name)
        elif processes.stop(component, grace=grace):
            print(f"dop {verb}: stopped {component.name}", flush=True)
            stopped.append(component.name)
    return stopped


def stop_cluster(driver: ClusterDriver, dry_run: bool = False) -> int:
    try:
        state = driver.state()
        if state == "absent":
            print("dop down: cluster absent; nothing to stop", flush=True)
            return 0
        if state == "stopped":
            print("dop down: cluster already stopped", flush=True)
            return 0
        driver.stop()
    except ProcessError as exc:
        print(f"dop down: {exc}", flush=True)
        return 1
    if dry_run:
        return 0
    print("dop down: cluster stopped; data is kept", flush=True)
    return 0


def run(
    config: Config,
    args: argparse.Namespace,
    *,
    runner: Callable[..., process.Result] | None = None,
    kube_factory: Callable[..., Kube] = Kube,
    tool_check: Callable[[list[str]], None] | None = None,
    processes: ProcessManager | None = None,
) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    processes = processes or ProcessManager(config.project.state_dir)
    driver = driver_for(config, runner=runner, kube_factory=kube_factory, dry_run=dry_run)
    env = config.environment
    if not dry_run:
        # Exactly the commands `down` runs: the state probes, the kubeconfig it
        # hands to `cluster_stop`, and `cluster_stop` itself. `down` runs no
        # kubectl of its own, so `kubectl` is not required here.
        (tool_check or process.tool_checker(config.root))(
            [c for c in (env.cluster_exists, env.cluster_running, env.kubeconfig, env.cluster_stop) if c]
        )

    def go() -> int:
        stop_owned_processes(config, processes=processes, dry_run=dry_run)
        rc = stop_cluster(driver, dry_run)
        return 0 if dry_run else rc

    if dry_run:
        return go()
    try:
        with processes.lock():
            return go()
    except LockHeld as exc:
        print(f"dop down: {exc}", flush=True)
        return 1
