"""`dop status` — the environment and every component; changes nothing.

Environment: cluster absent / stopped / running, namespace, every declared
workload (the environment's and each `cluster` component's) with ready/desired
from `kubectl get -o json`, the `addresses`. Then one line per component:
kind, state (`ready | not ready | down | n/a`), address; for `cluster` each
workload and the `ready` probe; for `process` the pid, the log path and
whether the `ready` url answers.

Exit: 0 all ready, 3 some not, 1 cluster absent or not running or kubectl
failing, 2 missing tool. `-v` appends the environment's `status` output verbatim.
"""

from __future__ import annotations

import argparse
from typing import Callable

from ..cluster import driver_for, environment_tools
from ..config import Component, Config
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube, WorkloadState, workload_ref
from ..probe import http_ready
from ..processes import ProcessManager

READY, NOT_READY, DOWN, NA, UNKNOWN = "ready", "not ready", "down", "n/a", "?"


def process_state(
    component: Component,
    *,
    processes: ProcessManager,
    prober: Callable[[str, bool], bool] | None = None,
    dry_run: bool = False,
) -> tuple[str, str]:
    """(state, detail) of a `process` component; read-only (a stale pid file is left alone).

    ``ready`` when alive and the ``ready`` url (if any) answers; ``not ready``
    when alive and it does not; ``down`` when not alive. The detail has the pid
    and the log path.
    """
    prober = prober or _probe
    log = processes.log_path(component)
    pid = processes.alive(component, cleanup=False)
    if pid is None:
        return DOWN, f"log {log}" if log.exists() else ""
    detail = f"pid {pid}  log {log}"
    if component.ready and not prober(component.ready, dry_run):
        return NOT_READY, detail
    return READY, detail


def _probe(url: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"WOULD WAIT ready {url}", flush=True)
        return True
    return http_ready(url)


def _workload_line(workload: str, states: dict[str, WorkloadState], dry_run: bool = False) -> tuple[str, bool]:
    if dry_run:
        return f"    {workload:<28} ?", True
    state = states.get(workload_ref(workload))
    if state is None:
        return f"    {workload:<28} absent", False
    return f"    {workload:<28} {state.ready}/{state.desired}", state.is_ready


def run(
    config: Config,
    args: argparse.Namespace,
    *,
    runner: Callable[..., process.Result] | None = None,
    kube_factory: Callable[..., Kube] = Kube,
    tool_check: Callable[[list[str]], None] | None = None,
    prober: Callable[[str, bool], bool] = _probe,
    processes: ProcessManager | None = None,
) -> int:
    dry_run = bool(getattr(args, "dry_run", False))
    verbose = bool(getattr(args, "verbose", False))
    environment = config.environment
    processes = processes or ProcessManager(config.project.state_dir)
    driver = driver_for(config, runner=runner, kube_factory=kube_factory, dry_run=dry_run)
    if not dry_run:
        fields = ["kubeconfig", "cluster_exists", "cluster_running"] + (["status"] if verbose else [])
        (tool_check or process.tool_checker(config.root))(environment_tools(environment, *fields))

    try:
        cluster = driver.state()
    except ProcessError as exc:
        print(f"dop status: {exc}", flush=True)
        return 1
    print(f"cluster    {cluster}")
    print(f"namespace  {environment.namespace}")

    states: dict[str, WorkloadState] = {}
    kubectl_failed = False
    if cluster == "running":
        try:
            states = {w.ref: w for w in driver.kube().get_workloads()}
        except ProcessError as exc:
            print(f"dop status: {exc}", flush=True)
            kubectl_failed = True

    verdicts: list[bool] = []
    if cluster == "running" and not kubectl_failed:
        print("workloads")
        for workload in environment.workloads:
            line, ok = _workload_line(workload, states, dry_run)
            print(line)
            verdicts.append(ok)
    if environment.addresses:
        print("addresses")
        for address in environment.addresses:
            print(f"    {address}")

    print("components")
    width = max([len(name) for name in config.components] + [4])
    for component in config.components.values():
        detail_lines: list[str] = []
        if component.kind == "none":
            state = NA
        elif component.kind == "cluster":
            if cluster != "running" or kubectl_failed:
                state = DOWN
            else:
                oks, present = [], []
                for workload in component.workloads:
                    line, ok = _workload_line(workload, states, dry_run)
                    detail_lines.append(line)
                    oks.append(ok)
                    present.append(workload_ref(workload) in states)
                probe_ok = True
                if component.ready:
                    probe_ok = prober(component.ready, dry_run)
                    detail_lines.append(f"    {'ready ' + component.ready:<28} {'answers' if probe_ok else 'no answer'}")
                if dry_run:
                    state = UNKNOWN
                elif not any(present):
                    state = DOWN
                elif all(oks) and probe_ok:
                    state = READY
                else:
                    state = NOT_READY
            verdicts.append(state == READY)
        else:
            state, detail = process_state(component, processes=processes, prober=prober, dry_run=dry_run)
            if detail:
                detail_lines.append(f"    {detail}")
            verdicts.append(state == READY)
        line = f"  {component.name:<{width}}  {component.kind:<8} {state:<10}"
        if component.address:
            line += f" {component.address}"
        print(line.rstrip())
        for detail in detail_lines:
            print(detail)

    if verbose:
        if cluster == "running":
            try:
                driver.env_status()
            except ProcessError as exc:
                print(f"dop status: {exc}", flush=True)
        else:
            print(f"(cluster {cluster}; the environment's status command was not run)")

    if dry_run:
        return 0
    if cluster != "running" or kubectl_failed:
        return 1
    return 0 if all(verdicts) else 3
