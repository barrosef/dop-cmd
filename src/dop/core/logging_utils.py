"""Logging utilities with redaction."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from .fs import ensure_dir
from .security import redact, guard_text


class Logger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path

    def _write(self, stream, level: str, message: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        line = f"[{timestamp}] {level} {message}"
        guard_text(line)
        safe = redact(line)
        ensure_dir(self.log_path.parent)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(safe + "\n")
        print(safe, file=stream)

    def info(self, message: str) -> None:
        self._write(sys.stdout, "INFO", message)

    def warn(self, message: str) -> None:
        self._write(sys.stderr, "WARN", message)

    def error(self, message: str) -> None:
        self._write(sys.stderr, "ERROR", message)


class NullLogger(Logger):
    def __init__(self) -> None:
        super().__init__(Path("/dev/null"))

    def _write(self, stream, level: str, message: str) -> None:
        safe = redact(f"[{level}] {message}")
        print(safe, file=stream)



def build_log_path(jira_key: str, command: str, *, logs_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}-{command}.log"
    return logs_dir / filename


def get_logger(jira_key: str, command: str, *, logs_dir: Path) -> Logger:
    return Logger(build_log_path(jira_key, command, logs_dir=logs_dir))
