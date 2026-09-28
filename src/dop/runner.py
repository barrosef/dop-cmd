"""Host containers for builds and test runners (B21, B22, B35). Never in the cluster.

Environment values never appear on the command line: each is passed as `-e KEY` with the value in
the docker client's environment, and dry-run prints keys only (B30).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class Mount:
    src: Path | str  # on the host
    dst: str  # in the container
    readonly: bool = False

    def arg(self) -> str:
        return f"{self.src}:{self.dst}" + (":ro" if self.readonly else "")


class Runner:
    def __init__(self, dry_run: bool, out: TextIO):
        self.dry_run = dry_run
        self.out = out

    def argv(
        self,
        image: str,
        mounts: Iterable[Mount | tuple] = (),
        env: Mapping[str, str] | None = None,
        network: str | None = None,
        workdir: str | None = None,
        command: Sequence[str] = (),
        *,
        add_hosts: Mapping[str, str] | None = None,
        user: str | None = None,
        name: str | None = None,
    ) -> list[str]:
        argv = ["docker", "run", "--rm"]
        if name:
            argv += ["--name", name]
        if user:
            argv += ["--user", user]
        if network:
            argv += ["--network", network]
        for hostname, ip in (add_hosts or {}).items():
            argv += ["--add-host", f"{hostname}:{ip}"]
        for m in mounts:
            m = m if isinstance(m, Mount) else Mount(*m)
            argv += ["-v", m.arg()]
        for key in (env or {}):
            argv += ["-e", key]
        if workdir:
            argv += ["-w", workdir]
        return [*argv, image, *command]

    def run(
        self,
        image: str,
        mounts: Iterable[Mount | tuple] = (),
        env: Mapping[str, str] | None = None,
        network: str | None = None,
        workdir: str | None = None,
        command: Sequence[str] = (),
        *,
        add_hosts: Mapping[str, str] | None = None,
        user: str | None = None,
        name: str | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        """`docker run --rm` on the host. Returns the process; the caller judges its exit code.

        network: e.g. "host" (B21, B35). add_hosts: hostname -> IP, e.g. every demand hostname a
        suite needs -> 127.0.0.1 (B21). capture=False streams the container's output.
        Dry-run prints the command (env keys only) and returns exit 0 with empty output.
        """
        argv = self.argv(image, mounts, env, network, workdir, command,
                         add_hosts=add_hosts, user=user, name=name)
        if self.dry_run:
            print(f"[dry-run] {shlex.join(argv)}", file=self.out)
            return subprocess.CompletedProcess(argv, 0, "", "")
        proc_env = {**os.environ, **(env or {})}
        return subprocess.run(argv, env=proc_env, capture_output=capture, text=True)
