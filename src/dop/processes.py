"""Processes `dop` owns, and the lock that serialises `up`, `down` and `reset`.

The lock serialises `up`, `down` and `reset`; `<name>.pid` and `<name>.log`
under the state directory record the `process` components `dop` started.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .config import Component
from .core import process
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


def _exists(pid: int) -> bool:
    """The process, or any member of the group it led, exists (a child of ours that exited is reaped first)."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    if _pid_alive(pid):
        return True
    try:
        os.killpg(pid, 0)
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

    # ------------------------------------------------------------------ owned processes

    def pid_path(self, component: Component) -> Path:
        return self.state_dir / f"{component.name}.pid"

    def log_path(self, component: Component) -> Path:
        return self.state_dir / f"{component.name}.log"

    def start(
        self,
        component: Component,
        *,
        root: Path,
        runner: Callable[..., process.Result] | None = None,
        dry_run: bool = False,
    ) -> int | None:
        """Detach ``component.run`` in its repo; write ``<name>.pid``; return the pid (None in dry-run)."""
        if not component.run:
            raise ProcessError(f"{component.name} declares no run command")
        runner = runner or process.run
        self.state_dir.mkdir(parents=True, exist_ok=True)
        result = runner(
            component.run,
            cwd=Path(root) / component.repo,
            env=dict(component.env),
            mode="detach",
            log_file=self.log_path(component),
            dry_run=dry_run,
        )
        if dry_run:
            return None
        if result.rc != 0 or result.pid is None:
            raise ProcessError(f"could not start {component.name} (rc {result.rc}): {component.run}")
        self.pid_path(component).write_text(f"{result.pid}\n", encoding="utf-8")
        return result.pid

    def alive(self, component: Component, *, cleanup: bool = True) -> int | None:
        """The pid in ``<name>.pid`` if that process (or its group) still exists, else None.

        A stale pid file is removed unless ``cleanup`` is False (read-only callers).
        """
        path = self.pid_path(component)
        pid = _read_pid(path)
        if pid is not None and _exists(pid):
            return pid
        if cleanup:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        return None

    def stop(self, component: Component, grace: float = 10) -> bool:
        """SIGTERM the process group, SIGKILL after ``grace`` seconds; drop the pid file. True if something was stopped."""
        pid = self.alive(component)
        if pid is None:
            return False
        process.kill_group(pid, grace=grace)
        try:
            self.pid_path(component).unlink()
        except FileNotFoundError:
            pass
        return True

    def owned(self) -> list[str]:
        """Names with a pid file, sorted."""
        if not self.state_dir.is_dir():
            return []
        return sorted(p.stem for p in self.state_dir.glob("*.pid"))
