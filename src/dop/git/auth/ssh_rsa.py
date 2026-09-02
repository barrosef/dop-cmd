from __future__ import annotations

import os
from pathlib import Path

from .base import GitAuthProvider
from ...core.errors import ValidationError


class SshRsaAuth(GitAuthProvider):
    DEFAULT_KEY_PATH = Path.home() / ".ssh" / "id_rsa"

    def __init__(self, *, ssh_key_env: str | None = None):
        self._ssh_key_env = ssh_key_env

    def _key_path(self) -> Path:
        if self._ssh_key_env:
            path_str = os.environ.get(self._ssh_key_env)
            if not path_str:
                raise ValidationError(f"Missing SSH key env var: {self._ssh_key_env}")
            return Path(path_str)
        return self.DEFAULT_KEY_PATH

    def git_env(self) -> dict[str, str]:
        key_path = self._key_path()
        if not key_path.exists():
            raise ValidationError(f"SSH key not found: {key_path}")
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
        )
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def git_command_prefix(self) -> list[str]:
        return ["git"]
