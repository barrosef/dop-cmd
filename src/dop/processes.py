"""Processes `dop` owns, and the lock that serialises `up`, `down` and `reset`.

The lock lives here (S0b); starting, observing and stopping `process`
components arrives with the process side (S0c).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import Component
from .core.errors import ProcessError

LOCK_FILENAME = "lock"


class LockHeld(ProcessError):
    """Another live `dop` holds the lock (exit 1)."""

    def __init__(self, pid: int) -> None:
        super().__init__(f"another dop is running (pid {pid})")
        self.pid = pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and int(text) > 0 else None


class ProcessManager:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)

    @property
    def lock_path(self) -> Path:
        return self.state_dir / LOCK_FILENAME

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Hold ``<state_dir>/lock`` (our pid) for the block.

        A file whose pid is alive raises ``LockHeld``; a file whose pid is dead
        (or unreadable) is stale and replaced.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.lock_path
        me = os.getpid()
        for _ in range(3):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                holder = _read_pid(path)
                if holder is not None and holder != me and _pid_alive(holder):
                    raise LockHeld(holder) from None
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{me}\n")
            break
        else:
            raise ProcessError(f"could not take the lock {path}")
        try:
            yield
        finally:
            if _read_pid(path) == me:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    # The process side arrives in S0c.

    def start(self, component: Component) -> int:
        raise NotImplementedError("S0c")

    def alive(self, component: Component) -> bool:
        raise NotImplementedError("S0c")

    def stop(self, component: Component) -> None:
        raise NotImplementedError("S0c")

    def log_path(self, component: Component) -> Path:
        raise NotImplementedError("S0c")
