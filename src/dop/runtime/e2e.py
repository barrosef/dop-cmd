from __future__ import annotations
import re
from pathlib import Path

from ..config.schema import WorkspaceConfig
from ..core.errors import ValidationError

JIRA_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def suite_order(ws: WorkspaceConfig) -> list[str]:
    """Return e2e suite names in config order (frontend apps with `e2e_suite`)."""
    return [
        app.e2e_suite
        for app in ws.runtime.apps.values()
        if app.role == "frontend" and app.e2e_suite
    ]


def normalize_jira_filter(key: str) -> str:
    """Normalize a JIRA key to the file-name fragment used in e2e test files.

    Examples:
        OG-150     -> og_150
        SUOPT-3144 -> suopt_3144
    """
    return key.lower().replace("-", "_")


def resolve_e2e_target(token: str, *, known_suites: list[str]) -> dict:
    """Resolve a ``dop e2e <target>`` token into a structured descriptor.

    Returns a dict with ``"kind"`` in {"suite", "jira", "file"}.
    Raises :class:`ValidationError` when the token cannot be resolved.
    """
    if token in known_suites:
        return {"kind": "suite", "suites": [token], "filter": None}

    if JIRA_RE.match(token):
        return {"kind": "jira", "suites": None, "filter": normalize_jira_filter(token)}

    if "/" in token or token.endswith(".py"):
        return {"kind": "file", "suites": None, "filter": None, "path": token}

    raise ValidationError(
        f"Cannot resolve e2e target '{token}'. "
        f"Expected: suite name ({', '.join(known_suites)}), JIRA key (OG-123), or file path."
    )


def find_suites_for_jira(
    jira_filter: str,
    *,
    e2e_root: Path,
    suite_order: list[str],
) -> list[str]:
    """Return suites (in *suite_order*) that contain test files matching *jira_filter*.

    A test file matches when ``jira_filter`` appears anywhere in its filename.
    """
    matched: list[str] = []
    for suite in suite_order:
        tests_dir = e2e_root / suite / "tests"
        if not tests_dir.is_dir():
            continue
        for f in tests_dir.iterdir():
            if f.is_file() and jira_filter in f.name:
                matched.append(suite)
                break
    return matched


def next_run_number(report_dir: Path) -> int:
    """Return the next sequential run number for Allure reports."""
    if not report_dir.is_dir():
        return 1
    existing = [
        int(d.name.split("-")[1])
        for d in report_dir.iterdir()
        if d.is_dir() and d.name.startswith("run-") and d.name.split("-")[1].isdigit()
    ]
    return max(existing, default=0) + 1
