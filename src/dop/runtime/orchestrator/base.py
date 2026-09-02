from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ServiceStatus:
    name: str               # nome lógico do app (ou serviço de infra)
    service: str            # nome do serviço no backend
    state: str | None       # running | exited | ...
    health: str | None      # healthy | starting | ...
    port: int | None
    up: bool


class RuntimeProvider(ABC):
    """Abstração de orquestrador de runtime (docker_compose, k8s, ...).

    Opera sobre nomes LÓGICOS de app; a implementação mapeia para serviços do
    backend. `infra` é sempre incluída em `up`.
    """

    @abstractmethod
    def up(self, apps: list[str], *, build: bool = False, wait: bool = True,
           dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def stop(self, apps: list[str], *, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def restart(self, apps: list[str], *, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def status(self, *, dry_run: bool = False, logger=None) -> list[ServiceStatus]: ...

    @abstractmethod
    def logs(self, apps: list[str], *, follow: bool = True, tail: int | None = None,
             since: str | None = None, dry_run: bool = False, logger=None) -> None: ...

    @abstractmethod
    def run_ephemeral(self, args: list[str], *, env: dict[str, str],
                      dry_run: bool = False, logger=None,
                      exec_replace: bool = False) -> int: ...

    @abstractmethod
    def run_service(self, service: str, args: list[str], *, profile: str | None = None,
                    workdir: str | None = None, env: dict[str, str] | None = None,
                    dry_run: bool = False, logger=None) -> int: ...

    @abstractmethod
    def clean(self, categories: list[str], *, dry_run: bool = False, logger=None) -> list[str]: ...
