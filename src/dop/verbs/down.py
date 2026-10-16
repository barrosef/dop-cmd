"""`dop down` — stops the app processes and the local cluster; data is kept.

Every `process` component `dop` owns is stopped first (S0c), then the
environment's `cluster_stop`. The next `dop up` finds the cluster stopped and
runs `cluster_start`. Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
from typing import Callable

from ..cluster import ClusterDriver, driver_for, environment_tools
from ..config import Config
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube
from ..processes import LockHeld, ProcessManager


def stop_owned_processes(config: Config, *, processes: ProcessManager, dry_run: bool) -> list[str]:
    """Stop every `process` component `dop` owns; return the names stopped. S0c."""
    raise NotImplementedError("S0c")


def stop_processes_or_note(config: Config, *, processes: ProcessManager, dry_run: bool, verb: str) -> None:
    """Call the process hook; until S0c lands, say in one line that nothing was stopped."""
    try:
        stopped = stop_owned_processes(config, processes=processes, dry_run=dry_run)
    except NotImplementedError:
        print(f"dop {verb}: no owned processes stopped (the process side arrives in S0c)", flush=True)
        return
    for name in stopped:
        print(f"dop {verb}: stopped {name}", flush=True)


def stop_cluster(driver: ClusterDriver) -> int:
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
    if not dry_run:
        (tool_check or process.require_tools)(
            environment_tools(config.environment, "cluster_exists", "cluster_running", "cluster_stop")[:-1]
        )

    def go() -> int:
        stop_processes_or_note(config, processes=processes, dry_run=dry_run, verb="down")
        rc = stop_cluster(driver)
        return 0 if dry_run else rc

    if dry_run:
        return go()
    try:
        with processes.lock():
            return go()
    except LockHeld as exc:
        print(f"dop down: {exc}", flush=True)
        return 1
