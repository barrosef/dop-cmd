"""`dop reset` — starts the local environment over; all local data is lost.

Stop the processes `dop` owns, then the environment's `down` (the namespace and
its volumes go), then everything `dop up` does. The cluster is brought to
running first, because `down` talks to it.
"""

from __future__ import annotations

import argparse
from typing import Callable

from ..cluster import driver_for, environment_tools
from ..config import Config
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube
from ..processes import LockHeld, ProcessManager
from .down import stop_owned_processes
from ..probe import http_ready
from .up import ENV_TOOLS, _ready_wait, _wait_alive, bring_up


def run(
    config: Config,
    args: argparse.Namespace,
    *,
    runner: Callable[..., process.Result] | None = None,
    kube_factory: Callable[..., Kube] = Kube,
    tool_check: Callable[[list[str]], None] | None = None,
    ready_wait: Callable[[str, float, bool], bool] = _ready_wait,
    processes: ProcessManager | None = None,
    probe: Callable[[str], bool] = http_ready,
    waiter: Callable[..., bool] = _wait_alive,
) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    processes = processes or ProcessManager(config.project.state_dir)
    driver = driver_for(config, runner=runner, kube_factory=kube_factory, dry_run=dry_run)
    injected = tool_check
    tool_check = tool_check or process.tool_checker(config.root)

    def tool_check_in(directory):
        return injected or process.tool_checker(directory)

    if not dry_run:
        # Everything the reset will run, checked before `down` destroys any data:
        # a missing `pnpm` found after the namespace is gone is a reset half done.
        tool_check(environment_tools(config.environment, *ENV_TOOLS, "down"))
        for component in config.components.values():
            if component.kind == "process":
                tool_check_in(config.root / component.repo)([c for c in (component.setup, component.run) if c])

    def go() -> int:
        stop_owned_processes(config, processes=processes, dry_run=dry_run, verb="reset")
        try:
            driver.ensure_running()
            driver.env_down()
        except ProcessError as exc:
            print(f"dop reset: {exc}", flush=True)
            return 0 if dry_run else 1
        return bring_up(
            config, [], driver=driver, processes=processes, dry_run=dry_run,
            ready_wait=ready_wait, tool_check=injected,
            runner=runner, probe=probe, waiter=waiter,
        )

    if dry_run:
        return go()
    try:
        with processes.lock():
            return go()
    except LockHeld as exc:
        print(f"dop reset: {exc}", flush=True)
        return 1
