#!/usr/bin/env python3
"""Askpass helper for Git HTTPS authentication."""

from __future__ import annotations

import os
import sys


def _get_creds() -> tuple[str, str]:
    login = os.environ.get("GIT_LOGIN") or os.environ.get("GIT_OPTUM_LOGIN", "")
    token = os.environ.get("GIT_TOKEN") or os.environ.get("GIT_OPTUM_TOKEN", "")
    return login, token


def main() -> int:
    prompt = " ".join(sys.argv[1:]).lower()
    login, token = _get_creds()

    if not login or not token:
        return 1

    if "username" in prompt or "user name" in prompt:
        sys.stdout.write(login)
        return 0

    sys.stdout.write(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
