"""One writer per unit (B27), stale locks broken (B34).

A lock is `<workspace>/.dop/locks/<unit>.lock`, created exclusively, holding pid, host, command and
start time. A lock held by a process that is dead on this host is broken, and the fact reported.
A lock held by a live process, or by another host, fails the unit naming the holder.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from .outcome import Unit


class LockHeld(Exception):
    def __init__(self, unit: Unit, holder: dict):
        self.unit = unit
        self.holder = holder
        who = f"pid {holder.get('pid')} on {holder.get('host')}"
        if holder.get("command"):
            who += f" ({holder['command']})"
        super().__init__(f"{unit.label} is locked by {who}")


def lock_name(unit: Unit) -> str:
    raw = "__".join(p for p in (unit.kind, unit.demand or "", unit.name or "") if p)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw) + ".lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


@contextmanager
def lock(
    locks_dir: Path,
    unit: Unit,
    *,
    command: str = "",
    out: TextIO | None = None,
) -> Iterator[None]:
    """Hold the lock of `unit` for the duration of the block. Raises LockHeld."""
    locks_dir = Path(locks_dir)
    locks_dir.mkdir(parents=True, exist_ok=True)
    path = locks_dir / lock_name(unit)
    me = {"pid": os.getpid(), "host": socket.gethostname(), "command": command, "started": time.time()}
    host = me["host"]

    for _ in range(3):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _read(path)
            if holder is None:
                continue  # released between our attempt and the read
            pid = holder.get("pid")
            if holder.get("host") == host and isinstance(pid, int) and not _alive(pid):
                path.unlink(missing_ok=True)
                print(
                    f"{unit.label}: broke stale lock of dead pid {pid} ({holder.get('command') or '?'})",
                    file=out or sys.stderr,
                )
                continue
            raise LockHeld(unit, holder or {"pid": "?", "host": "?"})
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(me, fh)
        break
    else:
        raise LockHeld(unit, _read(path) or {"pid": "?", "host": "?"})

    try:
        yield
    finally:
        if (_read(path) or {}).get("pid") == me["pid"]:
            path.unlink(missing_ok=True)
