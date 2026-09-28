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


_UNRESERVED = re.compile(r"[A-Za-z0-9.-]")


def _quote(part: str) -> str:
    """Percent-encode everything but letters, digits, `.` and `-` (D13/B27): a blanket
    char -> "_" replacement lets two distinct names collide (e.g. a suite "e2e/x" and one
    literally named "e2e_x"); percent-encoding is reversible-shaped and never does that."""
    return "".join(c if _UNRESERVED.match(c) else f"%{ord(c):02X}" for c in part)


def lock_name(unit: Unit) -> str:
    parts = [p for p in (unit.kind, unit.demand or "", unit.name or "") if p]
    return "__".join(_quote(p) for p in parts) + ".lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read(path: Path) -> tuple[dict | None, tuple[int, int] | None]:
    """The holder, and the (dev, ino) identity of the exact file read (D12): both come from one
    fd, so a rename done between this read and a later decision can always be told apart from
    ours -- the moved file's identity will no longer match what we read here."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None, None
    try:
        st = os.fstat(fd)
        data = os.read(fd, 1 << 20)
    finally:
        os.close(fd)
    ident = (st.st_dev, st.st_ino)
    try:
        return json.loads(data.decode("utf-8")), ident
    except (UnicodeDecodeError, ValueError):
        return {}, ident


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
            holder, ident = _read(path)
            if holder is None:
                continue  # released between our attempt and the read
            pid = holder.get("pid")
            if holder.get("host") == host and isinstance(pid, int) and not _alive(pid):
                # D12: renaming the stale lock aside is the atomic step -- os.rename is atomic on
                # one filesystem, so only the process whose rename actually moves `path` can see
                # it succeed; a racing process renaming the same (now-gone) path gets
                # FileNotFoundError and simply retries, never believing it broke anything.
                # That alone is not enough: `path` may have been replaced (unlinked, then
                # recreated) by whoever we thought was dead, between our read above and this
                # rename -- our rename would then unknowingly steal *their* live lock. `ident`
                # was read from the same fd as `holder`, atomically; compare it to the identity
                # of the file we actually moved before trusting it was still the dead one.
                stale = path.with_name(f"{path.name}.stale-{os.getpid()}-{time.time_ns()}")
                try:
                    os.rename(path, stale)
                except FileNotFoundError:
                    continue  # someone else's rename won the race; retry from the top
                moved_st = stale.stat()
                if (moved_st.st_dev, moved_st.st_ino) != ident:
                    # not the file we read: put the (live) lock back and fail naming its holder,
                    # rather than silently discarding someone else's active lock.
                    os.rename(stale, path)
                    live_holder, _ = _read(path)
                    raise LockHeld(unit, live_holder or holder)
                stale.unlink(missing_ok=True)
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
        holder, _ = _read(path)
        raise LockHeld(unit, holder or {"pid": "?", "host": "?"})

    try:
        yield
    finally:
        holder, _ = _read(path)
        if (holder or {}).get("pid") == me["pid"]:
            path.unlink(missing_ok=True)
