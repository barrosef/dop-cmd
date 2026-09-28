"""Artifacts into the cluster node, by copy (B6), replacing contents in place (B16, B24).

The node never sees the host disk. A directory is staged inside the node with `docker cp`,
validated there, and only then is the mounted directory made exactly equal to it in one pass —
every old entry removed (symlinks as links, never followed), the staging copied without
dereferencing, the directory itself never swapped (a hostPath mount keeps pointing at it).
"""

from __future__ import annotations

import posixpath
import shlex
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from .config import Workspace

# The scripts below run inside the node (BusyBox sh; cp/find/chmod are GNU). Every path arrives as
# a positional argument, never interpolated into the script text, and no file name ever passes
# through a shell word or line loop: removal and copy are whole-tree operations (D1).

# Refuses a destination that resolves anywhere but its own spelled path: an ancestor or the
# directory itself being a symlink would carry the removal and the copy out of node_root (D2).
# Checked on the nearest existing ancestor before creating anything, then on the directory.
_GUARD_DEST = r"""
p="$dest"
while [ ! -e "$p" ] && [ ! -L "$p" ]; do p="${p%/*}"; [ -n "$p" ] || p=/; done
[ "$(cd -P -- "$p" && pwd -P)" = "$p" ] || { echo "$p: resolves elsewhere; refusing" >&2; exit 1; }
mkdir -p -- "$dest"
cd -P -- "$dest"
[ "$(pwd -P)" = "$dest" ] || { echo "$dest: resolves elsewhere; refusing" >&2; exit 1; }
"""

# Validation of the staged copy ($1): it is a real directory holding at least one regular file.
# Prints its first entry when valid, nothing otherwise. Never names the destination (B16).
_CHECK_STAGED = r"""
[ -d "$1" ] && [ ! -L "$1" ] && find "$1" -type f -print -quit | grep -q . && ls -A "$1" | head -1
"""

# $1 = validated staging dir, $2 = destination dir. Makes the destination's contents exactly the
# staging's, in one pass (B24): every entry of the destination goes, symlinks as links (rm never
# follows them, find does not either), then the staging is copied without dereferencing (-P, D2);
# the directory itself stays, so a hostPath mount keeps pointing at it. World-readable afterwards
# (workloads run unprivileged); chmod -R does not follow symlinks.
_SYNC_SCRIPT = r"""
set -eu
src="$1"; dest="$2"
""" + _GUARD_DEST + r"""
find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a -P -- "$src/." "$dest/"
chmod -R a+rX -- "$dest"
"""

# $1 = validated staging dir, $2 = destination dir. Creates the destination if absent and copies
# the staging's index.html in only while the destination has none; nothing already there
# (published reports) is ever removed or overwritten (B16).
_SEED_SCRIPT = r"""
set -eu
src="$1"; dest="$2"
""" + _GUARD_DEST + r"""
if [ ! -e index.html ] && [ ! -L index.html ]; then
  cp -a -P -- "$src/index.html" "$dest/index.html"
  chmod a+r -- index.html
fi
"""


class NodeError(Exception):
    """A copy into the node failed; the destination was not touched unless stated."""


class Node:
    def __init__(self, ws: Workspace, dry_run: bool, out: TextIO):
        self.ws = ws
        self.dry_run = dry_run
        self.out = out

    @property
    def container(self) -> str:
        return self.ws.cluster.node

    def path(self, *parts: str) -> str:
        """A path inside the node under node_root, e.g. path(namespace, app) (B6)."""
        return posixpath.join(self.ws.cluster.node_root, *parts)

    def _inside_root(self, path: str) -> str:
        norm = posixpath.normpath(path)
        root = self.ws.cluster.node_root
        if not norm.startswith(root + "/") or ".." in norm.split("/"):
            raise NodeError(f"{path}: outside {root}")
        return norm

    def _docker(self, args: list[str]) -> subprocess.CompletedProcess:
        argv = ["docker", *args]
        if self.dry_run:
            print(f"[dry-run] {shlex.join(argv)}", file=self.out)
            return subprocess.CompletedProcess(argv, 0, "", "")
        try:
            proc = subprocess.run(argv, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise NodeError("docker not found on PATH") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            raise NodeError(f"docker {args[0]}: exit {proc.returncode}" + (f": {detail}" if detail else ""))
        return proc

    def _exec(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return self._docker(["exec", self.container, "sh", "-c", script, "sh", *args])

    @contextmanager
    def _staged(self, src_dir_on_host: Path | str, dest: str) -> Iterator[str]:
        """Copy a host directory into a fresh staging directory in the node and validate it there;
        yields the staging path, removed afterwards whatever happened. Raises NodeError before
        anything outside the staging is touched: absent or file-less on the host, copy failed,
        or found file-less once staged (B16, D3)."""
        src = Path(src_dir_on_host)
        if not src.is_dir():
            raise NodeError(f"{src}: artifact directory absent")
        if not any(src.iterdir()):
            raise NodeError(f"{src}: artifact directory empty")
        if not any(p.is_file() for p in src.rglob("*")):
            raise NodeError(f"{src}: artifact directory empty (no file in it)")
        staging = self.path(".staging", uuid.uuid4().hex)

        self._exec('mkdir -p "$1"', staging)
        try:
            self._docker(["cp", f"{src}/.", f"{self.container}:{staging}"])
            if not self.dry_run:
                check = self._docker(["exec", self.container, "sh", "-c", _CHECK_STAGED, "sh", staging])
                if not check.stdout.strip():
                    raise NodeError(f"{staging}: staged copy empty; {dest} left as it was")
            yield staging
        finally:
            try:
                self._exec('rm -rf "$1"', staging)
            except NodeError as exc:
                print(f"warning: staging {staging} not removed: {exc}", file=self.out)

    def sync(self, src_dir_on_host: Path | str, dest_dir_in_node: str) -> None:
        """Make `dest_dir_in_node` exactly equal to `src_dir_on_host` (B24).

        The whole source is staged in the node and validated before the destination is touched;
        any failure up to then leaves it as it was (B16, D3). Dry-run checks the source and prints
        the rest.
        """
        dest = self._inside_root(dest_dir_in_node)
        with self._staged(src_dir_on_host, dest) as staging:
            self._exec(_SYNC_SCRIPT, staging, dest)

    def seed(self, src_dir_on_host: Path | str, dest_dir_in_node: str) -> None:
        """Create `dest_dir_in_node` if absent and copy `src_dir_on_host`'s index.html into it only
        while it has none; nothing already there is ever removed or overwritten (B16)."""
        dest = self._inside_root(dest_dir_in_node)
        with self._staged(src_dir_on_host, dest) as staging:
            self._exec(_SEED_SCRIPT, staging, dest)

    def remove(self, dir_in_node: str) -> None:
        """Delete a directory under node_root (down, B10). Refuses anything outside it."""
        self._exec('rm -rf "$1"', self._inside_root(dir_in_node))
