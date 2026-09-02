from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ...config.schema import WorkspaceConfig
from ...core.errors import ProcessError, ValidationError
from ...core.process import run_command
from ..compose import (
    build_up_command, build_stop_command, build_logs_command,
    build_run_command, build_ps_command,
)
from .base import RuntimeProvider, ServiceStatus


class DockerComposeProvider(RuntimeProvider):
    def __init__(self, ws: WorkspaceConfig):
        self.ws = ws
        self.cfg = ws.runtime.docker_compose
        if self.cfg is None:
            raise ValidationError(
                "Missing [runtime.docker_compose] config for the 'docker_compose' orchestrator."
            )

    # --- helpers ---
    def _root(self) -> Path:
        return Path(self.ws.root)

    def _compose_file(self) -> Path:
        return self._root() / self.cfg.compose_file

    def _env_files(self) -> list[Path]:
        return [self._root() / e for e in self.cfg.env_files]

    def _service(self, app_name: str) -> str:
        app = self.ws.runtime.apps.get(app_name)
        return app.service if app else app_name

    # --- lifecycle ---
    def up(self, apps, *, build=False, wait=True, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps}) + list(self.ws.runtime.infra)
        cmd = build_up_command(
            compose_file=self._compose_file(),
            env_files=self._env_files(),
            services=services, wait=wait, build=build,
        )
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def stop(self, apps, *, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = build_stop_command(compose_file=self._compose_file(), services=services)
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def restart(self, apps, *, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = ["docker", "compose", "-f", str(self._compose_file()), "restart"] + services
        run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)

    def status(self, *, dry_run=False, logger=None) -> list[ServiceStatus]:
        cmd = build_ps_command(compose_file=self._compose_file())
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {' '.join(cmd)}")
            return []
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self._root()))
        containers: list[dict] = []
        for line in (result.stdout or "").strip().splitlines():
            try:
                containers.append(json.loads(line))
            except json.JSONDecodeError:
                pass

        def _match(service: str):
            return next(
                (c for c in containers if c.get("Name") == service or c.get("Service") == service),
                None,
            )

        statuses: list[ServiceStatus] = []
        for app_name, app in self.ws.runtime.apps.items():
            m = _match(app.service)
            statuses.append(ServiceStatus(
                name=app_name, service=app.service,
                state=(m or {}).get("State"), health=((m or {}).get("Health") or None),
                port=app.port, up=m is not None,
            ))
        for infra in self.ws.runtime.infra:
            m = _match(infra)
            statuses.append(ServiceStatus(
                name=infra, service=infra,
                state=(m or {}).get("State"), health=((m or {}).get("Health") or None),
                port=None, up=m is not None,
            ))
        return statuses

    def logs(self, apps, *, follow=True, tail=None, since=None, dry_run=False, logger=None) -> None:
        services = sorted({self._service(a) for a in apps})
        cmd = build_logs_command(
            compose_file=self._compose_file(), services=services,
            follow=follow, tail=tail, since=since,
        )
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {' '.join(cmd)}")
            return
        os.execvp(cmd[0], cmd)  # pragma: no cover

    def run_ephemeral(self, args, *, env, dry_run=False, logger=None, exec_replace=False) -> int:
        runner = self.cfg.ephemeral_runner
        if runner is None:
            raise ValidationError(
                "No ephemeral_runner configured in [runtime.docker_compose.ephemeral_runner]."
            )
        cmd = build_run_command(
            compose_file=self._compose_file(),
            env_files=None if exec_replace else self._env_files(),
            service=runner.service, args=args, profile=runner.profile, extra_env=env,
        )
        if exec_replace:
            if dry_run:
                if logger:
                    logger.info(f"WOULD RUN: {' '.join(cmd)}")
                return 0
            os.execvp(cmd[0], cmd)  # pragma: no cover
            return 0  # pragma: no cover
        try:
            run_command(cmd, cwd=self._root(), dry_run=dry_run, logger=logger)
            return 0
        except ProcessError:
            return 1

    def run_service(self, service, args, *, profile=None, workdir=None, env=None,
                    dry_run=False, logger=None) -> int:
        from ...core.security import guard_text
        cmd = build_run_command(
            compose_file=self._compose_file(),
            env_files=self._env_files(),
            service=service, args=args, profile=profile,
            extra_env=env, workdir=workdir,
        )
        display = " ".join(cmd)
        guard_text(display)
        if dry_run:
            if logger:
                logger.info(f"WOULD RUN: {display}")
            return 0
        if logger:
            logger.info(f"RUN: {display}")
        result = subprocess.run(cmd, cwd=str(self._root()))
        return result.returncode

    def clean(self, categories, *, dry_run=False, logger=None) -> list[str]:
        clean_map = self.cfg.clean
        cats = list(clean_map.keys()) if "all" in categories else categories
        volumes: list[str] = []
        for cat in cats:
            volumes += clean_map.get(cat, [])
        prefix = f"{self.cfg.project_name}_" if self.cfg.project_name else ""
        for vol in volumes:
            if not dry_run:
                subprocess.run(["docker", "volume", "rm", "-f", f"{prefix}{vol}"], capture_output=True)
            if logger:
                logger.info(f"Removed volume: {vol}")
        return volumes
