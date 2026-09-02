from __future__ import annotations

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError
from .base import PlatformProvider, PRResult


def build_platform_provider(workspace: WorkspaceConfig) -> PlatformProvider:
    platform = workspace.platform
    if platform == "azure_devops":
        from .azure import AzurePlatform

        return AzurePlatform(workspace)
    if platform == "github":
        from .github import GitHubPlatform

        return GitHubPlatform(workspace)
    if platform == "gitlab":
        from .gitlab import GitLabPlatform

        return GitLabPlatform(workspace)
    raise ValidationError(f"Unknown platform: {platform}")


__all__ = ["PlatformProvider", "PRResult", "build_platform_provider"]
