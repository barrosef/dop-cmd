from __future__ import annotations

import os
from pathlib import Path

from .base import GitAuthProvider
from ...core.errors import ValidationError


class TokenAuth(GitAuthProvider):
    def __init__(self, *, login_env: str, token_env: str, askpass_path: Path):
        self._login_env = login_env
        self._token_env = token_env
        self._askpass_path = askpass_path

    def _credentials(self) -> tuple[str, str]:
        login = os.environ.get(self._login_env)
        token = os.environ.get(self._token_env)
        if not login or not token:
            raise ValidationError(
                f"Missing credentials. Set env vars: {self._login_env}, {self._token_env}"
            )
        return login, token

    def git_env(self) -> dict[str, str]:
        login, token = self._credentials()
        if not self._askpass_path.exists():
            raise ValidationError(f"Missing GIT_ASKPASS helper: {self._askpass_path}")
        env = os.environ.copy()
        env[self._login_env] = login
        env[self._token_env] = token
        # Provide generic names for askpass to consume.
        env["GIT_LOGIN"] = login
        env["GIT_TOKEN"] = token
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = str(self._askpass_path)
        return env

    def git_command_prefix(self) -> list[str]:
        return ["git", "-c", "credential.helper="]
