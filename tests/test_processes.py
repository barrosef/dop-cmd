from __future__ import annotations

import os
import subprocess

import pytest

from dop.processes import LockHeld, ProcessManager


def test_lock_holds_our_pid_and_releases(tmp_path):
    pm = ProcessManager(tmp_path / ".dop")
    with pm.lock():
        assert pm.lock_path.read_text().strip() == str(os.getpid())
    assert not pm.lock_path.exists()


def test_live_holder_refuses(tmp_path):
    pm = ProcessManager(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        pm.lock_path.write_text(f"{holder.pid}\n")
        with pytest.raises(LockHeld, match=f"another dop is running \\(pid {holder.pid}\\)"):
            with pm.lock():
                pytest.fail("entered a held lock")
        assert pm.lock_path.read_text().strip() == str(holder.pid)
    finally:
        holder.kill()
        holder.wait()


def test_stale_file_is_replaced(tmp_path):
    pm = ProcessManager(tmp_path)
    dead = subprocess.Popen(["true"])
    dead.wait()
    pm.lock_path.write_text(f"{dead.pid}\n")
    with pm.lock():
        assert pm.lock_path.read_text().strip() == str(os.getpid())


@pytest.mark.parametrize("content", ["", "garbage"])
def test_unreadable_file_is_stale(tmp_path, content):
    pm = ProcessManager(tmp_path)
    pm.lock_path.write_text(content)
    with pm.lock():
        assert pm.lock_path.read_text().strip() == str(os.getpid())
