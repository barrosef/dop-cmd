from __future__ import annotations

import os

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError


def expand_apps(
    ws: WorkspaceConfig,
    tokens: list[str],
    *,
    no_deps: bool = False,
) -> set[str]:
    """Resolve alias tokens to canonical app names and auto-add `depends_on`.

    Args:
        ws: Workspace configuration with runtime apps and aliases.
        tokens: App names or aliases provided by the user.
        no_deps: When True, skip automatic dependency injection.

    Returns:
        Set of canonical app names.

    Raises:
        ValidationError: If any token does not resolve to a known app.
    """
    aliases = ws.runtime.aliases
    apps = ws.runtime.apps

    resolved: set[str] = set()
    for token in tokens:
        name = aliases.get(token, token)
        if name not in apps:
            available_apps = ", ".join(sorted(apps))
            available_aliases = ", ".join(sorted(aliases))
            raise ValidationError(
                f"App '{token}' not found. "
                f"Available apps: {available_apps} "
                f"(aliases: {available_aliases})"
            )
        resolved.add(name)

    if not no_deps:
        # Fixpoint: add transitive depends_on of every resolved app.
        changed = True
        while changed:
            changed = False
            for name in list(resolved):
                for dep in apps[name].depends_on:
                    if dep in apps and dep not in resolved:
                        resolved.add(dep)
                        changed = True

    return resolved


def infer_urls(
    ws: WorkspaceConfig,
    requested: set[str],
) -> dict[str, str]:
    """Infer backend URL environment variables for the requested app set.

    For every backend app that declares ``url_env``:
      - if it is in *requested*, inject the local service URL
        ``http://<service>:<port>``;
      - otherwise inject the value of ``fallback_url_env`` from the
        environment, when present.

    Returns:
        Dict of env var name -> URL string.
    """
    env: dict[str, str] = {}
    for app in ws.runtime.apps.values():
        if app.role != "backend" or not app.url_env:
            continue
        if app.name in requested:
            env[app.url_env] = f"http://{app.service}:{app.port}"
        elif app.fallback_url_env:
            fallback = os.environ.get(app.fallback_url_env, "")
            if fallback:
                env[app.url_env] = fallback
    return env
