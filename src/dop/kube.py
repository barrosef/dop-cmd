"""Every kubectl call. Each names the configured context (B25); the current context is never read.
In dry-run a mutating call is printed, never run (B15); reads run, so resolution is the same."""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import TextIO

from .config import Workspace
from .outcome import UsageError


class KubeError(Exception):
    """A kubectl call failed (the cluster could not be read or written)."""


class Kube:
    def __init__(self, ws: Workspace, dry_run: bool, out: TextIO):
        self.ws = ws
        self.dry_run = dry_run
        self.out = out
        self._context_checked = False

    @property
    def context(self) -> str:
        return self.ws.cluster.context

    def argv(self, args: list[str]) -> list[str]:
        return ["kubectl", "--context", self.context, *args]

    def ensure_context(self) -> None:
        """B25: a configured context that does not exist in the kubeconfig is exit 2."""
        if self._context_checked:
            return
        try:
            proc = subprocess.run(
                self.argv(["config", "get-contexts", self.context, "-o", "name"]),
                capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise UsageError("kubectl not found on PATH") from exc
        if proc.returncode != 0 or proc.stdout.strip() != self.context:
            raise UsageError(f"kube context {self.context!r} does not exist (cluster.context in dop.toml)")
        self._context_checked = True

    def run(
        self,
        args: list[str],
        *,
        read: bool = False,
        input: str | None = None,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run `kubectl --context <ctx> <args>`.

        read=True marks a call that changes nothing: it runs in dry-run too. Any other call in
        dry-run is printed and a successful empty result returned. check=True raises KubeError on
        a non-zero exit. capture=False streams output to the terminal (e.g. logs).
        """
        self.ensure_context()
        argv = self.argv(args)
        if self.dry_run and not read:
            print(f"[dry-run] {shlex.join(argv)}", file=self.out)
            return subprocess.CompletedProcess(argv, 0, "", "")
        proc = subprocess.run(argv, input=input, capture_output=capture, text=True)
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip() if capture else ""
            raise KubeError(f"{shlex.join(args)}: exit {proc.returncode}" + (f": {detail}" if detail else ""))
        return proc

    def json(self, args: list[str]) -> dict:
        """A read returning JSON (`-o json` is appended). Empty output gives {}."""
        proc = self.run([*args, "-o", "json"], read=True)
        text = proc.stdout.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise KubeError(f"{shlex.join(args)}: not JSON: {exc}") from exc
