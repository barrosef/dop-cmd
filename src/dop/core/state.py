"""State management for dop CLI (v0.5 — fluxo conversacional, sem stages; e2e + runtime fields)."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import StateError, ValidationError
from .fs import ensure_dir, read_json, write_json

ALIAS_FILE_NAME = ".alias"

_E2E_HISTORY_MAX = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_jira_key(jira_key: str, *, pattern: str = r"^[A-Z][A-Z0-9]+-\d+$") -> None:
    if not re.match(pattern, jira_key):
        raise ValidationError(f"Invalid JIRA key format: {jira_key}")


def _default_e2e() -> dict[str, Any]:
    return {
        "max_strikes": 3,
        "strike_count": 0,
        "history": [],
        "last_run": None,
    }


def _default_runtime() -> dict[str, Any]:
    return {
        "apps_up": [],
    }


def default_state(jira_key: str, jira_url: str | None = None, *, jira_base_url: str | None = None) -> dict[str, Any]:
    """Create a default state dict.

    Accepts two calling conventions:
    - New: default_state("OG-100", "https://jira/OG-100")  — full URL as second positional arg
    - Old: default_state("OG-100", jira_base_url="https://jira")  — keyword base URL
    """
    if jira_url is not None:
        resolved_url = jira_url
    elif jira_base_url is not None:
        resolved_url = f"{jira_base_url}/{jira_key}"
    else:
        raise ValueError("Either jira_url or jira_base_url must be provided")
    return {
        "jiraKey": jira_key,
        "jiraUrl": resolved_url,
        "linkedJiraKeys": [],
        "createdAt": now_iso(),
        "repos": {},
        "prs": [],
        "commands_log": [],
        "e2e": _default_e2e(),
        "runtime": _default_runtime(),
    }


def ensure_state_defaults(
    state: dict[str, Any],
    jira_key: str | None = None,
    *,
    jira_base_url: str | None = None,
) -> dict[str, Any]:
    """Populate missing keys for backward compatibility (v0.4 → v0.5).

    Accepts two calling conventions:
    - New: ensure_state_defaults(state)  — infers keys from state itself
    - Old: ensure_state_defaults(state, jira_key, jira_base_url="https://jira")
    """
    resolved_key = jira_key or state.get("jiraKey", "")
    if jira_base_url is not None:
        resolved_url = f"{jira_base_url}/{resolved_key}"
    else:
        resolved_url = state.get("jiraUrl", f"https://jira/{resolved_key}")

    state.setdefault("jiraKey", resolved_key)
    state.setdefault("jiraUrl", resolved_url)
    state.setdefault("linkedJiraKeys", [])
    state.setdefault("createdAt", now_iso())
    state.setdefault("repos", {})
    state.setdefault("prs", [])
    state.setdefault("commands_log", [])
    if not isinstance(state["repos"], dict):
        state["repos"] = {}
    if not isinstance(state["prs"], list):
        state["prs"] = []
    if not isinstance(state["linkedJiraKeys"], list):
        state["linkedJiraKeys"] = []
    # v0.5 additions
    state.setdefault("e2e", _default_e2e())
    e2e = state["e2e"]
    e2e.setdefault("max_strikes", 3)
    e2e.setdefault("strike_count", 0)
    e2e.setdefault("history", [])
    e2e.setdefault("last_run", None)
    state.setdefault("runtime", _default_runtime())
    state["runtime"].setdefault("apps_up", [])
    return state


# ---------------------------------------------------------------------------
# E2E helpers
# ---------------------------------------------------------------------------

def record_e2e_run(
    state: dict[str, Any],
    *,
    status: str,
    exit_code: int,
    suites_run: list[str],
    filter_used: str | None = None,
    headed: bool = False,
    report_url: str | None = None,
) -> None:
    """Record an e2e run result, updating strike_count and history.

    - status "red"   → increments strike_count
    - status "green" → resets strike_count to 0
    History is capped at _E2E_HISTORY_MAX entries (oldest removed first).
    """
    ensure_state_defaults(state)
    e2e = state["e2e"]
    entry: dict[str, Any] = {
        "at": now_iso(),
        "status": status,
        "exit_code": exit_code,
        "suites_run": suites_run,
        "filter_used": filter_used,
        "headed": headed,
        "report_url": report_url,
    }
    e2e["last_run"] = entry
    e2e["history"].append(entry)
    if len(e2e["history"]) > _E2E_HISTORY_MAX:
        e2e["history"] = e2e["history"][-_E2E_HISTORY_MAX:]
    if status == "green":
        e2e["strike_count"] = 0
    else:
        e2e["strike_count"] = e2e.get("strike_count", 0) + 1


def reset_strikes(state: dict[str, Any]) -> None:
    """Reset strike_count to 0 (human said 'tenta de novo')."""
    ensure_state_defaults(state)
    state["e2e"]["strike_count"] = 0


def get_strike_count(state: dict[str, Any]) -> tuple[int, int]:
    """Return (current_strike_count, max_strikes)."""
    ensure_state_defaults(state)
    e2e = state["e2e"]
    return e2e["strike_count"], e2e["max_strikes"]


def load_state(
    jira_key: str,
    state_path: Path,
    *,
    jira_base_url: str,
    create: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_jira_key(jira_key)
    if not state_path.exists():
        state = default_state(jira_key, jira_base_url=jira_base_url)
        original = copy.deepcopy(state)
        if create:
            ensure_dir(state_path.parent)
            write_json(state_path, state)
        return state, original
    state = read_json(state_path) or default_state(jira_key, jira_base_url=jira_base_url)
    state = ensure_state_defaults(state, jira_key, jira_base_url=jira_base_url)
    original = copy.deepcopy(state)
    return state, original


def save_state(
    state_path: Path,
    state: dict[str, Any],
    original_state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> None:
    ensure_state_defaults(
        state,
        state.get("jiraKey", ""),
        jira_base_url=state.get("jiraUrl", "").rsplit("/", 1)[0],
    )
    assert_commands_log_append_only(
        original_state.get("commands_log", []), state.get("commands_log", [])
    )
    write_json(state_path, state, dry_run=dry_run)


def assert_commands_log_append_only(original: list[Any], new: list[Any]) -> None:
    if len(new) < len(original):
        raise StateError("commands_log cannot be truncated")
    if new[: len(original)] != original:
        raise StateError("commands_log is append-only; existing entries cannot change")


def append_command_log(
    state: dict[str, Any],
    command: str,
    *,
    user: str | None = None,
    notes: str | None = None,
) -> None:
    entry: dict[str, Any] = {"command": command, "at": now_iso()}
    if user:
        entry["user"] = user
    if notes:
        entry["notes"] = notes
    state.setdefault("commands_log", []).append(entry)


def set_repo_impacted(state: dict[str, Any], repo_name: str, branch: str | None = None) -> None:
    repo_entry = state.setdefault("repos", {}).setdefault(repo_name, {})
    repo_entry["impacted"] = True
    if branch:
        repo_entry["branch"] = branch


def set_repo_skipped(state: dict[str, Any], repo_name: str) -> None:
    state.setdefault("repos", {}).setdefault(repo_name, {})["impacted"] = False


def impacted_repos(state: dict[str, Any]) -> list[str]:
    return [name for name, cfg in state.get("repos", {}).items() if cfg.get("impacted")]


def add_linked_jira(state: dict[str, Any], linked_key: str) -> bool:
    """Adiciona linked_key em linkedJiraKeys se ainda não estiver. Retorna True se foi adicionado."""
    linked = state.setdefault("linkedJiraKeys", [])
    if linked_key in linked or linked_key == state.get("jiraKey"):
        return False
    linked.append(linked_key)
    return True


def append_pr_record(state: dict[str, Any], pr_record: dict[str, Any]) -> None:
    """Adiciona um registro de PR no histórico (sem deduplicação — é log)."""
    state.setdefault("prs", []).append({**pr_record, "createdAt": pr_record.get("createdAt", now_iso())})


# ---------------------------------------------------------------------------
# Alias resolution (multi-Jira grouping)
# ---------------------------------------------------------------------------

def alias_path(demands_root: Path, jira_key: str) -> Path:
    return demands_root / jira_key / ALIAS_FILE_NAME


def write_alias(demands_root: Path, alias_key: str, master_key: str, *, dry_run: bool = False) -> Path:
    """Cria docs/RFC/<alias_key>/.alias contendo a chave mestre."""
    target = alias_path(demands_root, alias_key)
    if dry_run:
        return target
    ensure_dir(target.parent)
    target.write_text(f"{master_key}\n", encoding="utf-8")
    return target


def resolve_alias(demands_root: Path, jira_key: str) -> str:
    """Se docs/RFC/<jira_key>/.alias existe, retorna a chave mestre referenciada; senão jira_key."""
    p = alias_path(demands_root, jira_key)
    if not p.exists():
        return jira_key
    content = p.read_text(encoding="utf-8").strip()
    if not content:
        raise ValidationError(f"Alias file empty: {p}")
    return content.splitlines()[0].strip()
