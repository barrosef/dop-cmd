"""`dop logs <component>` — follows a component's logs.

- `cluster`: the component's `logs` command, streamed, with the environment's
  `KUBECONFIG`, from the project root.
- `process`: `tail -f` of the log `dop` keeps; never started -> exit 3.
- `none`: nothing to follow -> exit 3.

Ctrl-C ends the follow with exit 0; a follow that ends with a non-zero return
code is exit 1.
"""

from __future__ import annotations

import argparse
import shlex
from typing import Callable

from ..cluster import driver_for, environment_tools
from ..config import Config, ConfigError
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube
from ..processes import ProcessManager


def _follow(runner: Callable[..., process.Result], cmd: str, config: Config, env: dict[str, str] | None, dry_run: bool) -> int:
    try:
        result = runner(cmd, cwd=config.root, env=env, mode="stream", dry_run=dry_run)
    except KeyboardInterrupt:
        print(flush=True)
        return 0
    return 0 if result.rc == 0 else 1


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
    component = config.components.get(args.component)
    if component is None:
        raise ConfigError(
            f"unknown component '{args.component}' (declared: {', '.join(config.components) or 'none'})"
        )

    if component.kind == "none":
        print(f"dop logs: {component.name} has no runtime; nothing to follow", flush=True)
        return 3

    runner = runner or process.run

    if component.kind == "process":
        processes = processes or ProcessManager(config.project.state_dir)
        log = processes.log_path(component)
        if not dry_run and not log.exists():
            print(f"dop logs: {component.name} was never started (no {log})", flush=True)
            return 3
        return _follow(runner, f"tail -f {shlex.quote(str(log))}", config, None, dry_run)

    if not component.logs:
        print(f"dop logs: {component.name} declares no logs command; nothing to follow", flush=True)
        return 3
    if not dry_run:
        (tool_check or process.require_tools)(environment_tools(config.environment, "kubeconfig") + [component.logs])
    driver = driver_for(config, runner=runner, kube_factory=kube_factory, dry_run=dry_run)
    try:
        env = {"KUBECONFIG": driver.kubeconfig_path()}
    except ProcessError as exc:
        print(f"dop logs: {exc}", flush=True)
        return 1
    return _follow(runner, component.logs, config, env, dry_run)
