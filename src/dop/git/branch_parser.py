from __future__ import annotations

import re
from pathlib import Path

from ..core.errors import ValidationError

_HEADING_RE = re.compile(r"^#{1,6}\s+Branch\s+de\s+Trabalho\s*$", re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_SEPARATOR_RE = re.compile(r"^\|[-:|\s]+\|$")


def parse_branch_table(plan_path: Path) -> dict[str, str]:
    """
    Le 02-plan-00.md e retorna {repo_name: branch_name}.
    Lanca ValidationError se a secao nao for encontrada ou estiver vazia.
    """
    content = plan_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    in_section = False
    in_table = False
    result: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()

        if _HEADING_RE.match(stripped):
            in_section = True
            in_table = False
            continue

        if not in_section:
            continue

        if not stripped:
            continue

        if stripped.startswith("#"):
            break

        if _SEPARATOR_RE.match(stripped):
            in_table = True
            continue

        if in_table:
            match = _TABLE_ROW_RE.match(stripped)
            if not match:
                continue
            repo = match.group(1).strip()
            branch = match.group(2).strip()
            if repo and branch and repo.lower() != "repo":
                result[repo] = branch

    if not in_section:
        raise ValidationError("Branch table section not found in plan.")
    if not result:
        raise ValidationError("Branch table section is empty.")
    return result
