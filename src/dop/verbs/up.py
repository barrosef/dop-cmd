"""`dop up [component…]` — the environment and the named components, with what they need.

No argument: the environment and every component. With arguments: the named
components, their `needs` closure, and the environment if any of them is a
`cluster` component.

1. Cluster: absent → `cluster_up` + every `prepare`; stopped → `cluster_start`.
2. Environment: `up`, then a rollout wait on the environment's own `workloads`.
3. Components in dependency order: `cluster` → rollout wait on its `workloads`,
   then the `ready` probe if declared; `process` → `process_up` (setup, detach `run`, probe `ready` while the pid lives);
   `none` → nothing to bring up.
4. One line per unit (outcome, address), then the environment's `addresses`.

Exit: 0 all done, 3 some failed or skipped, 1 none done or the environment
failed, 2 configuration or missing tool.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..cluster import ClusterDriver, driver_for, environment_tools
from ..config import Component, Config, closure
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube
from ..probe import http_ready, wait_until
from ..processes import LockHeld, ProcessManager

DONE, FAILED, SKIPPED = "done", "failed", "skipped"
ENV_TOOLS = ("kubeconfig", "cluster_exists", "cluster_running", "cluster_up", "cluster_start", "prepare", "up")


@dataclass(frozen=True)
class Outcome:
    unit: str
    outcome: str
    address: str | None = None
    detail: str = ""


def _wait_alive(fn: Callable[[], bool], timeout: float, alive: Callable[[], bool]) -> bool:
    return wait_until(fn, timeout, alive=alive)


def process_up(
    component: Component,
    *,
    processes: ProcessManager,
    root: Path,
    timeout: float,
    dry_run: bool,
    runner: Callable[..., process.Result] | None = None,
    tool_check: Callable[[list[str]], None] | None = None,
    probe: Callable[[str], bool] = http_ready,
    waiter: Callable[[Callable[[], bool], float, Callable[[], bool]], bool] = _wait_alive,
) -> str:
    """Start a `process` component and wait until it is ready; return a detail line.

    Raises ``ProcessError`` (the unit is *failed*) when the port is held by a
    process `dop` does not own, setup fails, the process dies, or it is not
    ready in time. A dead pid is never ready.
    """
    runner = runner or process.run
    if not dry_run:
        if component.ready and probe(component.ready) and processes.alive(component) is None:
            raise ProcessError("port held by a process dop does not own")
        pid = processes.alive(component)
        if pid is not None:
            return f"already running, pid {pid}"
        (tool_check or process.require_tools)([c for c in (component.setup, component.run) if c])

    if component.setup:
        result = runner(component.setup, cwd=Path(root) / component.repo, mode="stream", dry_run=dry_run)
        if result.rc != 0:
            raise ProcessError(f"setup failed (rc {result.rc}): {component.setup}")
    pid = processes.start(component, root=root, runner=runner, dry_run=dry_run)
    if dry_run:
        if component.ready:
            print(f"WOULD WAIT ready {component.ready}", flush=True)
        return ""
    log = processes.log_path(component)
    if component.ready:
        ready = waiter(
            lambda: probe(component.ready),
            timeout,
            lambda: processes.alive(component) is not None,
        )
        if not ready:
            if processes.alive(component) is None:
                raise ProcessError(f"process exited (see {log})")
            raise ProcessError(f"not ready after {timeout:g}s (see {log})")
        return f"ready, pid {pid}"
    if processes.alive(component) is None:
        raise ProcessError(f"process exited (see {log})")
    return f"started, pid {pid}"


def _ready_wait(url: str, timeout: float, dry_run: bool) -> bool:
    if dry_run:
        print(f"WOULD WAIT ready {url}", flush=True)
        return True
    return wait_until(lambda: http_ready(url), timeout)


def _print(outcomes: list[Outcome], addresses: list[str]) -> None:
    width = max([len(o.unit) for o in outcomes] + [11])
    for o in outcomes:
        line = f"{o.unit:<{width}}  {o.outcome:<7}"
        if o.address:
            line += f"  {o.address}"
        if o.detail:
            line += f"  ({o.detail})"
        print(line.rstrip(), flush=True)
    if addresses:
        print("addresses:")
        for address in addresses:
            print(f"  {address}")


def _exit_code(outcomes: list[Outcome]) -> int:
    done = sum(1 for o in outcomes if o.outcome == DONE)
    if done == len(outcomes):
        return 0
    return 3 if done else 1


def bring_up(
    config: Config,
    names: list[str],
    *,
    driver: ClusterDriver,
    processes: ProcessManager,
    dry_run: bool,
    ready_wait: Callable[[str, float, bool], bool] = _ready_wait,
    tool_check: Callable[[list[str]], None] | None = None,
    runner: Callable[..., process.Result] | None = None,
    probe: Callable[[str], bool] = http_ready,
    waiter: Callable[..., bool] = _wait_alive,
) -> int:
    """§3.1 without the lock (`reset` calls it while holding its own)."""
    components = closure(config, names or list(config.components))
    needs_environment = not names or any(c.kind == "cluster" for c in components)
    environment = config.environment

    if needs_environment and not dry_run:
        (tool_check or process.require_tools)(environment_tools(environment, *ENV_TOOLS))

    outcomes: list[Outcome] = []
    if needs_environment:
        try:
            driver.ensure_running()
            driver.env_up()
            driver.wait_environment()
        except ProcessError as exc:
            outcomes.append(Outcome("environment", FAILED, detail=str(exc)))
            outcomes += [Outcome(c.name, SKIPPED, c.address, "environment failed") for c in components]
            _print(outcomes, [])
            return 0 if dry_run else 1
        outcomes.append(Outcome("environment", DONE))

    status: dict[str, str] = {}
    for component in components:
        blocked = [n for n in component.needs if status.get(n) != DONE]
        if blocked:
            outcome = Outcome(component.name, SKIPPED, component.address, f"needs {', '.join(blocked)}")
        elif component.kind == "none":
            outcome = Outcome(component.name, DONE, component.address, "nothing to bring up")
        elif component.kind == "cluster":
            outcome = _cluster_up(component, driver, environment.wait_timeout, dry_run, ready_wait)
        else:
            try:
                detail = process_up(
                    component, processes=processes, root=config.root, timeout=environment.wait_timeout,
                    dry_run=dry_run, runner=runner, tool_check=tool_check, probe=probe, waiter=waiter,
                )
                outcome = Outcome(component.name, DONE, component.address, detail)
            except ProcessError as exc:
                outcome = Outcome(component.name, FAILED, component.address, str(exc))
        status[component.name] = outcome.outcome
        outcomes.append(outcome)

    _print(outcomes, environment.addresses if needs_environment else [])
    if dry_run:
        return 0
    if needs_environment:
        # The environment is done here; the verdict is the components'.
        return _exit_code(outcomes[1:]) if len(outcomes) > 1 else 0
    return _exit_code(outcomes)


def _cluster_up(
    component: Component,
    driver: ClusterDriver,
    timeout: float,
    dry_run: bool,
    ready_wait: Callable[[str, float, bool], bool],
) -> Outcome:
    try:
        driver.wait_workloads(component.workloads)
    except ProcessError as exc:
        return Outcome(component.name, FAILED, component.address, str(exc))
    if component.ready and not ready_wait(component.ready, timeout, dry_run):
        return Outcome(component.name, FAILED, component.address, f"{component.ready} did not answer within {timeout:g}s")
    return Outcome(component.name, DONE, component.address)


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
    names = list(getattr(args, "components", None) or [])
    closure(config, names)  # an unknown name is a configuration error before anything runs
    processes = processes or ProcessManager(config.project.state_dir)
    driver = driver_for(config, runner=runner, kube_factory=kube_factory, dry_run=dry_run)

    def go() -> int:
        return bring_up(
            config, names, driver=driver, processes=processes, dry_run=dry_run,
            ready_wait=ready_wait, tool_check=tool_check, runner=runner, probe=probe, waiter=waiter,
        )

    if dry_run:
        return go()
    try:
        with processes.lock():
            return go()
    except LockHeld as exc:
        print(f"dop up: {exc}", flush=True)
        return 1
