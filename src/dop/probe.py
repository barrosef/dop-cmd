"""Readiness probes: one HTTP answer, and a poll until something holds.

Minimal on purpose.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from typing import Callable


def http_ready(url: str, timeout: float = 3) -> bool:
    """True when ``url`` answers with a 2xx or 3xx status; any other answer or no answer is False."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - URLs come from dop.toml
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_until(
    fn: Callable[[], bool],
    timeout: float,
    interval: float = 2,
    alive: Callable[[], bool] | None = None,
) -> bool:
    """Poll ``fn`` until it returns True (True) or ``timeout`` seconds pass (False).

    When ``alive`` is given and returns False the wait gives up at once: a
    process that died is never ready.
    """
    deadline = time.monotonic() + timeout
    while True:
        if alive is not None and not alive():
            return False
        if fn():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
