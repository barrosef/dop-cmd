"""What a verb receives: the workspace, the filters, dry-run, where to print, and the shared tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, TextIO

from .config import Workspace
from .kube import Kube
from .lock import lock as _lock
from .node import Node
from .outcome import Unit, UsageError
from .runner import Runner

# ASCII only: `\d` would take any Unicode digit, and `$` a trailing newline (B7, D17).
_DEMAND_KEY = re.compile(r"[A-Za-z]+-[0-9]+")
# A key becomes a label value and a DNS label (B3, B4, B26); both stop at 63 characters.
_LABEL_MAX = 63


def demand_key(raw: str) -> str:
    """B7: a key as the operator gave it, validated and upper-cased."""
    if not _DEMAND_KEY.fullmatch(raw):
        raise UsageError(f"{raw!r} is not a demand key (expected like SUOPT-1530)")
    if len(raw) > _LABEL_MAX:
        raise UsageError(f"{raw!r} is longer than {_LABEL_MAX} characters; it cannot be a label (B4)")
    return raw.upper()


@dataclass(frozen=True)
class Filters:
    """B12: each narrows; several values on one filter unite; filters intersect.
    Empty means "no filter"."""

    tasks: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    repos: tuple[str, ...] = ()


@dataclass
class Context:
    ws: Workspace
    filters: Filters
    dry_run: bool
    out: TextIO
    # Verb-specific options from the command line (e.g. {"k": "expr", "follow": True}).
    options: dict[str, Any] = field(default_factory=dict)
    # The command as typed, for lock holders' records.
    command: str = ""

    @cached_property
    def kube(self) -> Kube:
        return Kube(self.ws, self.dry_run, self.out)

    @cached_property
    def node(self) -> Node:
        return Node(self.ws, self.dry_run, self.out)

    @cached_property
    def runner(self) -> Runner:
        return Runner(self.dry_run, self.out)

    def lock(self, unit: Unit):
        """Context manager holding the unit's lock (B27, B34); raises lock.LockHeld.
        Dry-run changes nothing, so it takes no lock."""
        if self.dry_run:
            from contextlib import nullcontext

            return nullcontext()
        return _lock(self.ws.state_dir / "locks", unit, command=self.command, out=self.out)
