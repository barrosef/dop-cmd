from __future__ import annotations
from pathlib import Path


def build_up_command(
    *,
    compose_file: Path,
    env_files: list[Path],
    services: list[str],
    wait: bool = True,
    build: bool = False,
    force_recreate: bool = False,
) -> list[str]:
    """Build a `docker compose up -d` command list."""
    cmd = ["docker", "compose", "-f", str(compose_file)]
    for ef in env_files:
        cmd += ["--env-file", str(ef)]
    cmd.append("up")
    cmd.append("-d")
    if wait:
        cmd.append("--wait")
    if build:
        cmd.append("--build")
    if force_recreate:
        cmd.append("--force-recreate")
    cmd += services
    return cmd


def build_stop_command(*, compose_file: Path, services: list[str]) -> list[str]:
    """Build a `docker compose stop` command list."""
    cmd = ["docker", "compose", "-f", str(compose_file), "stop"]
    cmd += services
    return cmd


def build_logs_command(
    *,
    compose_file: Path,
    services: list[str],
    follow: bool = True,
    tail: int | None = None,
    since: str | None = None,
) -> list[str]:
    """Build a `docker compose logs` command list."""
    cmd = ["docker", "compose", "-f", str(compose_file), "logs"]
    if follow:
        cmd.append("-f")
    if tail is not None:
        cmd += ["--tail", str(tail)]
    if since:
        cmd += ["--since", since]
    cmd += services
    return cmd


def build_run_command(
    *,
    compose_file: Path,
    env_files: list[Path] | None = None,
    service: str,
    args: list[str],
    profile: str | None = None,
    extra_env: dict[str, str] | None = None,
    workdir: str | None = None,
) -> list[str]:
    """Build a `docker compose run --rm` command list."""
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if profile:
        cmd += ["--profile", profile]
    if env_files:
        for ef in env_files:
            cmd += ["--env-file", str(ef)]
    cmd += ["run", "--rm"]
    for k, v in (extra_env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    if workdir:
        cmd += ["-w", workdir]
    cmd += [service] + args
    return cmd


def build_ps_command(*, compose_file: Path) -> list[str]:
    """Build a `docker compose ps --format json` command list."""
    return ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"]


def write_env_runtime(path: Path, env: dict[str, str]) -> None:
    """Write key=value pairs to a runtime .env file (sorted for determinism)."""
    lines = [f"{k}={v}" for k, v in sorted(env.items())]
    path.write_text("\n".join(lines) + "\n")
