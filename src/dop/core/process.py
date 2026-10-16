"""Run shell commands in the three modes the verbs need.

- ``stream``  foreground, inherits the terminal; returns the return code.
- ``capture`` stdout returned; on failure stdout and stderr are redacted.
- ``detach``  new session, stdout+stderr appended to ``log_file``; pid returned.

Every command is a shell string. ``dry_run`` prints ``WOULD RUN <cmd>`` and
returns rc 0 without running anything.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .errors import ToolMissing
from .security import redact

MODES = ("stream", "capture", "detach")

# Words that are not executables on PATH and must not be looked up.
_SHELL_BUILTINS = {"cd", "export", "exec", "set", "unset", ".", "source", "eval", ":", "true", "false"}


@dataclass(frozen=True)
class Result:
    rc: int
    stdout: str = ""
    pid: int | None = None


def _merged_env(env: dict[str, str] | None) -> dict[str, str] | None:
    if not env:
        return None
    merged = dict(os.environ)
    merged.update(env)
    return merged


def run(
    cmd: str,
    *,
    cwd: Path | str,
    env: dict[str, str] | None = None,
    mode: str = "stream",
    log_file: Path | None = None,
    dry_run: bool = False,
) -> Result:
    """Run ``cmd`` through the shell in ``cwd``; ``env`` is added to the inherited environment."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {', '.join(MODES)})")
    if mode == "detach" and log_file is None:
        raise ValueError("detach mode needs a log_file")
    # A configured command is the project's own, not a secret dump: the guard
    # applies to captured output (``redact``), never to the command itself.

    if dry_run:
        print(f"WOULD RUN {cmd}", flush=True)
        return Result(rc=0)

    full_env = _merged_env(env)
    cwd = str(cwd)

    if mode == "stream":
        sys.stdout.flush()
        sys.stderr.flush()
        completed = subprocess.run(cmd, shell=True, cwd=cwd, env=full_env)
        return Result(rc=completed.returncode)

    if mode == "capture":
        completed = subprocess.run(cmd, shell=True, cwd=cwd, env=full_env, capture_output=True, text=True)
        if completed.returncode == 0:
            return Result(rc=0, stdout=completed.stdout)
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
        return Result(rc=completed.returncode, stdout=redact(output))

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as handle:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return Result(rc=0, pid=proc.pid)


def _reap(pid: int) -> None:
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_group(pid: int, grace: float = 10) -> None:
    """SIGTERM the process group led by ``pid``; SIGKILL whatever is left after ``grace`` seconds."""
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError(f"refusing to signal process group {pid!r}")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        _reap(pid)
        if not _group_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    for _ in range(40):
        _reap(pid)
        if not _group_alive(pid):
            return
        time.sleep(0.05)


def executable_of(cmd: str) -> str | None:
    """The first word of a shell command, skipping leading VAR=value assignments."""
    try:
        words = shlex.split(cmd)
    except ValueError:
        words = cmd.split()
    for word in words:
        if "=" in word and not word.startswith(("/", ".")) and word.split("=", 1)[0].isidentifier():
            continue
        return word
    return None


def require_tools(commands: Iterable[str], cwd: Path | str | None = None) -> None:
    """Raise ``ToolMissing`` naming the first command's executable that is not available.

    A first word containing ``/`` is a path, resolved against ``cwd`` (where the
    command will run; dop's own cwd when None) and must be an executable file;
    any other word must be on PATH.
    """
    for cmd in commands:
        tool = executable_of(cmd)
        if tool is None or tool in _SHELL_BUILTINS:
            continue
        if "/" in tool:
            path = Path(tool).expanduser()
            if not path.is_absolute():
                path = Path(cwd if cwd is not None else Path.cwd()) / path
            if not (path.is_file() and os.access(path, os.X_OK)):
                raise ToolMissing(tool)
        elif shutil.which(tool) is None:
            raise ToolMissing(tool)


def tool_checker(cwd: Path | str) -> "Callable[[list[str]], None]":
    """``require_tools`` bound to the directory the checked commands will run in."""
    return lambda commands: require_tools(commands, cwd=cwd)
