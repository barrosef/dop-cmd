"""`dop test <component> [--layer unit|integration|contract]`.

`setup` first if declared; then the layer's `forwards` are opened (detached,
waited until the local port answers); then the commands run in order inside the
component's `repo`, streaming. The first failing command fails the verb (exit 1).
Forwards are closed in `finally`, whatever happened. Nothing is retried,
skipped or masked.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable

from ..config import Config, ConfigError
from ..core import process
from ..core.errors import ProcessError
from ..kube import Kube

DEFAULT_LAYER = "unit"


def _say(message: str) -> None:
    print(f"dop test: {message}", flush=True)


def run(
    config: Config,
    args: argparse.Namespace,
    *,
    runner: Callable[..., process.Result] | None = None,
    kube_factory: Callable[..., Kube] = Kube,
    killer: Callable[[int], None] | None = None,
    tool_check: Callable[[list[str]], None] | None = None,
) -> int:
    runner = runner or process.run
    killer = killer or process.kill_group
    tool_check = tool_check or process.require_tools
    dry_run = bool(getattr(args, "dry_run", False))
    layer_name = getattr(args, "layer", None) or DEFAULT_LAYER

    component = config.components.get(args.component)
    if component is None:
        raise ConfigError(
            f"unknown component '{args.component}' (declared: {', '.join(config.components) or 'none'})"
        )
    layer = component.tests.get(layer_name)
    if layer is None:
        declared = ", ".join(component.tests) or "none"
        print(
            f"dop test: component '{component.name}' declares no '{layer_name}' test layer (declared: {declared})",
            file=sys.stderr,
        )
        return 2

    environment = config.environment if layer.forwards else None

    if not dry_run:
        needed = ([component.setup] if component.setup else []) + list(layer.commands)
        if environment is not None:
            needed += [environment.kubeconfig, "kubectl"]
        tool_check(needed)

    if component.setup:
        result = runner(component.setup, cwd=component.repo, mode="stream", dry_run=dry_run)
        if result.rc != 0:
            _say(f"{component.name} setup failed (rc {result.rc}): {component.setup}")
            return 1

    pids: list[int] = []
    try:
        if environment is not None:
            result = runner(environment.kubeconfig, cwd=config.root, mode="capture", dry_run=dry_run)
            if result.rc != 0:
                _say(f"kubeconfig command failed (rc {result.rc}): {environment.kubeconfig}")
                if result.stdout:
                    print(result.stdout, file=sys.stderr)
                return 1
            kube = kube_factory(
                result.stdout.strip(),
                environment.namespace,
                log_dir=config.project.state_dir,
                dry_run=dry_run,
                cwd=config.root,
            )
            for spec in layer.forwards:
                try:
                    pid = kube.port_forward(spec)
                except ProcessError as exc:
                    _say(str(exc))
                    return 1
                if pid is not None:
                    pids.append(pid)

        for cmd in layer.commands:
            result = runner(cmd, cwd=component.repo, mode="stream", dry_run=dry_run)
            if result.rc != 0:
                _say(f"{component.name} {layer_name} failed (rc {result.rc}): {cmd}")
                return 1
        _say(f"{component.name} {layer_name} " + ("dry run, nothing ran" if dry_run else "passed"))
        return 0
    finally:
        for pid in pids:
            killer(pid)
