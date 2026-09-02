from __future__ import annotations

from ...config.schema import WorkspaceConfig
from ...core.errors import ValidationError
from .base import RuntimeProvider, ServiceStatus
from .docker_compose import DockerComposeProvider

_NOT_IMPLEMENTED = {"kubernetes", "okd", "rancher"}


def build_runtime_provider(ws: WorkspaceConfig) -> RuntimeProvider:
    """Select the runtime orchestrator implementation from `runtime.orchestrator`."""
    orchestrator = ws.runtime.orchestrator
    if orchestrator == "docker_compose":
        return DockerComposeProvider(ws)
    if orchestrator in _NOT_IMPLEMENTED:
        raise ValidationError(
            f"orchestrator '{orchestrator}' ainda não implementado; use 'docker_compose'."
        )
    raise ValidationError(f"orchestrator desconhecido: '{orchestrator}'.")


__all__ = ["build_runtime_provider", "RuntimeProvider", "ServiceStatus", "DockerComposeProvider"]
