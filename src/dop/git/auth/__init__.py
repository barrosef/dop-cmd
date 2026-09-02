from __future__ import annotations

from pathlib import Path

from .base import GitAuthProvider
from ...config.schema import WorkspaceConfig
from ...core.errors import ValidationError


def build_auth_provider(workspace: WorkspaceConfig) -> GitAuthProvider:
    method = workspace.auth_method
    creds = workspace.credentials
    if method == "token":
        from .token import TokenAuth

        askpass = Path(__file__).resolve().parents[2] / "git_askpass.py"
        return TokenAuth(
            login_env=creds.login_env or "GIT_LOGIN",
            token_env=creds.token_env or "GIT_TOKEN",
            askpass_path=askpass,
        )
    if method == "ssh_rsa":
        from .ssh_rsa import SshRsaAuth

        return SshRsaAuth(ssh_key_env=creds.ssh_key_env)
    if method == "ssh_ed25519":
        from .ssh_ed25519 import SshEd25519Auth

        return SshEd25519Auth(ssh_key_env=creds.ssh_key_env)
    raise ValidationError(f"Unknown auth_method: {method}")


__all__ = ["GitAuthProvider", "build_auth_provider"]
