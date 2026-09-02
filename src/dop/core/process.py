"""Subprocess helpers with redaction and dry-run support."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Iterable

from .errors import ProcessError
from .security import guard_text, redact


def _format_command(cmd: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def run_command(
    cmd: Iterable[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
    logger=None,
) -> subprocess.CompletedProcess[str]:
    cmd_list = [str(part) for part in cmd]
    cmd_display = _format_command(cmd_list)
    guard_text(cmd_display)

    if dry_run:
        if logger:
            logger.info(f"WOULD RUN: {cmd_display}")
        return subprocess.CompletedProcess(cmd_list, 0, "", "")

    result = subprocess.run(
        cmd_list,
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
    )

    if logger:
        if result.stdout:
            logger.info(f"STDOUT: {result.stdout.strip()}")
        if result.stderr:
            logger.warn(f"STDERR: {result.stderr.strip()}")

    if result.returncode != 0:
        stdout = redact(result.stdout.strip()) if result.stdout else ""
        stderr = redact(result.stderr.strip()) if result.stderr else ""
        message = f"Command failed ({result.returncode}): {cmd_display}"
        if stdout:
            message += f" | stdout: {stdout}"
        if stderr:
            message += f" | stderr: {stderr}"
        raise ProcessError(message)

    return result
