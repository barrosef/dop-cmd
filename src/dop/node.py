"""Artifacts into the cluster node, by copy (B6), replacing contents in place (B16, B24).

The node never sees the host disk. A directory is staged inside the node with `docker cp`,
verified non-empty there, and only then is the mounted directory made exactly equal to it — stale
entries removed, the directory itself never swapped (a hostPath mount keeps pointing at it).
"""

from __future__ import annotations

import posixpath
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import TextIO

from .config import Workspace

# Runs inside the node (busybox sh). $1 = staging dir, $2 = destination dir.
# 1. remove from dest whatever staging does not have, or has with another type (dir vs non-dir);
# 2. copy staging over dest; 3. make it world-readable (workloads run unprivileged).
_SYNC_SCRIPT = r"""
set -eu
src="$1"; dest="$2"
mkdir -p "$dest"
cd "$dest"
find . -mindepth 1 -depth | while IFS= read -r p; do
  s="$src/$p"
  if [ ! -e "$s" ] && [ ! -L "$s" ]; then rm -rf "$p"; continue; fi
  if [ -d "$p" ] && [ ! -L "$p" ]; then
    if [ ! -d "$s" ] || [ -L "$s" ]; then rm -rf "$p"; fi
  elif [ -d "$s" ] && [ ! -L "$s" ]; then rm -f "$p"; fi
done
cp -a "$src/." "$dest/"
chmod -R a+rX "$dest"
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

    def sync(self, src_dir_on_host: Path | str, dest_dir_in_node: str) -> None:
        """Make `dest_dir_in_node` exactly equal to `src_dir_on_host` (B24).

        An absent or empty source raises NodeError before anything in the node is touched (B16);
        so does a staged copy found empty. Dry-run checks the source and prints the rest.
        """
        src = Path(src_dir_on_host)
        if not src.is_dir():
            raise NodeError(f"{src}: artifact directory absent")
        if not any(src.iterdir()):
            raise NodeError(f"{src}: artifact directory empty")
        dest = self._inside_root(dest_dir_in_node)
        staging = self.path(".staging", uuid.uuid4().hex)

        self._exec('mkdir -p "$1"', staging)
        try:
            self._docker(["cp", f"{src}/.", f"{self.container}:{staging}"])
            if not self.dry_run:
                check = self._docker(["exec", self.container, "sh", "-c", 'ls -A "$1" | head -1', "sh", staging])
                if not check.stdout.strip():
                    raise NodeError(f"{staging}: staged copy empty; {dest} left as it was")
            self._exec(_SYNC_SCRIPT, staging, dest)
        finally:
            try:
                self._exec('rm -rf "$1"', staging)
            except NodeError as exc:
                print(f"warning: staging {staging} not removed: {exc}", file=self.out)

    def remove(self, dir_in_node: str) -> None:
        """Delete a directory under node_root (down, B10). Refuses anything outside it."""
        self._exec('rm -rf "$1"', self._inside_root(dir_in_node))
