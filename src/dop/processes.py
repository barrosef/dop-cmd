"""Processes `dop` owns, and the lock that serialises `up`, `down` and `reset`.

The lock serialises `up`, `down` and `reset`; `<name>.pid` and `<name>.log`
under the state directory record the `process` components `dop` started.
"""

from __future__ import annotations

import json
import re
import os
import subprocess
import time
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


def _ps(field: str, pid: int) -> str | None:
    """``ps -o <field>= -p <pid>`` (POSIX, so Linux and macOS alike); None when ps is unavailable or has no such process."""
    try:
        done = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip()
    return out if done.returncode == 0 and out else None


def _executables(cmd: str) -> list[str]:
    """Executable names of a shell command: the first word of each `&&`/`||`/`;`/`|` segment, basenames, first segment first.

    A shell that execs the last command of ``a && b`` shows ``b`` in ``ps``, so the
    first word alone would call a live process a stranger.
    """
    names: list[str] = []
    for segment in re.split(r"&&|\|\||[;|\n]", cmd):
        exe = process.executable_of(segment)
        if exe and os.path.basename(exe) not in names:
            names.append(os.path.basename(exe))
    return names


def _read_record(path: Path) -> dict | None:
    """The pid file: JSON ``{pid, exe, started}``; a bare pid (older file) reads as a record with no identity."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.isdigit():
        return {"pid": int(text)} if int(text) > 0 else None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    pid = record.get("pid") if isinstance(record, dict) else None
    return record if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _identity_ok(record: dict) -> bool:
    """A pid is trusted only while the process behind it still looks like the one `dop` started."""
    pid = record["pid"]
    if not _pid_alive(pid):
        return True  # only the group's other members remain; the kernel does not reuse a pid that is a live group id
    exes = record.get("exes") or ([record["exe"]] if record.get("exe") else [])
    if exes:
        args = _ps("args", pid)
        if args is not None and not any(exe in args for exe in exes):
            return False
    started = record.get("lstart")
    if started:
        now = _ps("lstart", pid)
        if now is not None and now != started:
            return False
    return True


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
        if not dry_run:
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
        exes = _executables(component.run)
        record = {
            "pid": result.pid,
            "exe": exes[0] if exes else None,
            "exes": exes,
            "lstart": _ps("lstart", result.pid),
            "written": time.time(),
        }
        self.pid_path(component).write_text(json.dumps(record) + "\n", encoding="utf-8")
        return result.pid

    def alive(self, component: Component, *, cleanup: bool = True) -> int | None:
        """The pid in ``<name>.pid`` if that process (or its group) still exists, else None.

        A stale pid file is removed unless ``cleanup`` is False (read-only callers).
        """
        path = self.pid_path(component)
        record = _read_record(path)
        if record is not None and _exists(record["pid"]) and _identity_ok(record):
            return record["pid"]
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
