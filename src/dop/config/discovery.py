from __future__ import annotations

from pathlib import Path

from .schema import WorkspaceConfig
from .loader import load_config, config_path
from ..core.errors import ValidationError


def find_workspace_by_cwd(cwd: Path | None = None) -> WorkspaceConfig:
    """
    Procura a workspace cujo `root` eh ancestral do diretorio corrente.
    Lanca ValidationError se nenhuma ou mais de uma workspace for encontrada.
    """
    cwd = (cwd or Path.cwd()).resolve()
    workspaces = load_config()
    matches = []
    for ws in workspaces.values():
        ws_root = Path(ws.root).resolve()
        if cwd == ws_root or cwd.is_relative_to(ws_root):
            matches.append(ws)

    if not matches:
        raise ValidationError(
            f"No workspace found for directory: {cwd}\n"
            f"Configure workspaces in {config_path()} or use --workspace <name>."
        )
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValidationError(
            f"Multiple workspaces match directory {cwd}: {names}\n"
            "Use --workspace <name> to be explicit."
        )
    return matches[0]


def get_workspace(name: str | None, cwd: Path | None = None) -> WorkspaceConfig:
    """
    Retorna a workspace pelo nome (se fornecido) ou por CWD.
    """
    if name:
        workspaces = load_config()
        if name not in workspaces:
            raise ValidationError(f"Workspace '{name}' not found in config.")
        return workspaces[name]
    return find_workspace_by_cwd(cwd)
