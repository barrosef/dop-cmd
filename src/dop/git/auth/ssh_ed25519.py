from __future__ import annotations

from .ssh_rsa import SshRsaAuth


class SshEd25519Auth(SshRsaAuth):
    DEFAULT_KEY_PATH = SshRsaAuth.DEFAULT_KEY_PATH.parent / "id_ed25519"
