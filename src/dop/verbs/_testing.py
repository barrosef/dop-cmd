"""Shared support for `test aaa|it|e2e` and `report` (B21-B23, B29, B33, B35).

Nothing here is a contract fixed in step 1; it exists only so the four verb modules do not repeat
themselves. It reads scope.py's worktree helpers (never edits them) to find the demand's worktree
of the *workspace* repository (B22) -- distinct from the per-app repos in `[repos]`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..address import expand, host
from ..config import Workspace, url_refs
from ..outcome import Unit, UnitResult, UsageError, done, failed, planned, skipped
from ..runner import Mount, Runner
from ..scope import ScopeError, branch_matches, demand_apps, worktrees

if TYPE_CHECKING:
    from ..node import Node

_MAVEN_COMMAND = {
    "aaa": ("mvn", "-q", "test"),
    "it": ("mvn", "-q", "verify"),
}

_RUN_DIR = re.compile(r"^run-(\d+)$")

_ENV_READ = re.compile(
    r"os\.(?:environ\s*\[\s*|environ\.get\(\s*|getenv\(\s*)[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
)


# ---------------------------------------------------------------------------------------------
# test tree (B22)


def demand_test_tree(ws: Workspace, demand: str) -> Path:
    """The demand's worktree of the workspace repository (ws.root under git), else the main
    checkout. Raises ScopeError exactly as scope.py does for an app repo: git unreadable, more
    than one candidate worktree, or a found worktree whose path is gone (B28)."""
    found = [w for w in worktrees(ws.root) if not w.main and branch_matches(demand, w.branch)]  # B8 amended
    if not found:
        return ws.paths.test_root
    if len(found) > 1:
        names = ", ".join(f"{w.branch} ({w.path})" for w in found)
        raise ScopeError(f"workspace repo: more than one worktree for {demand}: {names}")
    wt = found[0]
    if not wt.path.is_dir():
        raise ScopeError(f"workspace repo: worktree of {demand} at {wt.path} no longer exists")
    try:
        rel = ws.paths.test_root.relative_to(ws.root)
    except ValueError:
        return ws.paths.test_root  # test_root lives outside the workspace root: not per-worktree
    return wt.path / rel


# ---------------------------------------------------------------------------------------------
# staging a test project's own copy (B22)


def stage(dest: Path, src: Path) -> None:
    """Replace `dest` with a fresh copy of `src`. Not called in dry-run."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


# ---------------------------------------------------------------------------------------------
# reports (B23): runs never pruned, regenerated on the host, synced to the node


def next_run_dir(project_reports_dir: Path) -> Path:
    """The next `run-<N>` under a demand/project's report directory; never reuses or deletes
    one (B16)."""
    n = 0
    if project_reports_dir.is_dir():
        for child in project_reports_dir.iterdir():
            m = _RUN_DIR.match(child.name)
            if m:
                n = max(n, int(m.group(1)))
    return project_reports_dir / f"run-{n + 1}"


def _runs_with_results(project_dir: Path) -> list[str]:
    """Names of every `run-<N>` under project_dir whose results/ actually holds something, in
    run order -- a run that produced nothing is not fed to allure."""
    if not project_dir.is_dir():
        return []
    found: list[tuple[int, str]] = []
    for child in project_dir.iterdir():
        m = _RUN_DIR.match(child.name)
        results = child / "results"
        if m and results.is_dir() and any(results.iterdir()):
            found.append((int(m.group(1)), child.name))
    return [name for _, name in sorted(found)]


def regenerate_report(ws: Workspace, runner: Runner, node: "Node", demand: str, project: str) -> str | None:
    """B23: allure generate over every run of demand/project into its site, then sync the site
    into the node's shared namespace. Never raises -- a failure here is a warning, it fails
    nothing already done."""
    project_dir = ws.paths.reports / demand / project
    runs = _runs_with_results(project_dir)
    if not runs:
        return None
    try:
        proc = runner.run(
            ws.runners.allure,
            mounts=[Mount(project_dir, "/report")],
            workdir="/report",
            command=["generate", *[f"{r}/results" for r in runs], "-o", "site", "--clean"],
        )
        if proc.returncode != 0:
            return f"allure generate for {demand}/{project} failed (exit {proc.returncode})"
        node.sync(project_dir / "site", node.path(ws.address.shared_namespace, "reports", demand, project))
        return None
    except Exception as exc:  # a report failure never fails work already done (B23)
        return f"report publish for {demand}/{project} failed: {exc}"


# ---------------------------------------------------------------------------------------------
# `test aaa` / `test it` (B22, B29, B35)


def _collect_allure_results(staged: Path, results_dir: Path) -> None:
    src = staged / "target" / "allure-results"
    if src.is_dir() and any(src.iterdir()):
        shutil.copytree(src, results_dir, dirs_exist_ok=True)


def run_repo_layer(ctx, unit: Unit, layer: str) -> UnitResult:
    """One (demand, repo) unit of `test aaa` or `test it`."""
    ws = ctx.ws
    demand = unit.demand
    repo = unit.repo or unit.name

    try:
        tree = demand_test_tree(ws, demand)
    except ScopeError as exc:
        return failed(unit, str(exc))

    src = tree / layer / repo
    if not src.is_dir():
        return skipped(unit, f"no {layer} test project for {repo} (B29)")

    project = f"{layer}-{repo}"
    notes = ["tests the test project's own code (B22)"] if layer == "it" else []

    if ctx.dry_run:
        return planned(unit, "; ".join([f"would run mvn {layer} for {repo}", *notes]))

    staged = ws.state_dir / "runs" / demand / project
    stage(staged, src)

    mounts = [
        Mount(staged, f"/w/test/{layer}/{repo}"),
        Mount(unit.path, f"/w/repos/{repo}", readonly=True),
    ]
    network = None
    if layer == "it":  # B35: Testcontainers started beside the runner must be reachable
        mounts.append(Mount("/var/run/docker.sock", "/var/run/docker.sock"))
        network = "host"

    proc = ctx.runner.run(
        ws.runners.maven, mounts=mounts, network=network,
        workdir=f"/w/test/{layer}/{repo}", command=_MAVEN_COMMAND[layer],
    )

    results_dir = next_run_dir(ws.paths.reports / demand / project) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    _collect_allure_results(staged, results_dir)

    warning = regenerate_report(ws, ctx.runner, ctx.node, demand, project)
    if warning:
        notes.append(f"warning: {warning}")

    if proc.returncode != 0:
        return failed(unit, "; ".join([f"mvn {layer} exit {proc.returncode}", *notes]))
    return done(unit, "; ".join(notes))


# ---------------------------------------------------------------------------------------------
# `test e2e` (B21, B33)


def _looks_like_url_key(key: str) -> bool:
    up = key.upper()
    return "URL" in up or up.endswith("_HOST") or up.endswith("_BASE")


_SKIP_DIRS = frozenset({".venv", "venv", "site-packages", "node_modules", "__pycache__", ".pytest_cache"})


def scan_env_url_keys(suite_dir: Path) -> set[str]:
    """Every URL-like key the suite's .py sources read from os.environ / os.getenv (B33)."""
    keys: set[str] = set()
    for f in suite_dir.rglob("*.py"):
        if _SKIP_DIRS.intersection(f.relative_to(suite_dir).parts):  # installed libraries are not the suite
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        keys.update(_ENV_READ.findall(text))
    return {k for k in keys if _looks_like_url_key(k)}


def run_suite_unit(ctx, unit: Unit) -> UnitResult:
    """One (demand, suite) unit of `test e2e`."""
    ws = ctx.ws
    demand, suite, appname = unit.demand, unit.name, unit.app
    app = ws.apps[appname]

    try:
        tree = demand_test_tree(ws, demand)
    except ScopeError as exc:
        return failed(unit, str(exc))

    suite_dir = tree / "e2e" / suite
    if not suite_dir.is_dir():
        return skipped(unit, f"no e2e suite directory for {suite}")

    missing = sorted(scan_env_url_keys(suite_dir) - set(app.suite_env))
    if missing:
        return failed(unit, f"suite reads {', '.join(missing)}, not covered by suite_env (B33)")

    if ctx.dry_run:
        return planned(unit, f"would run pytest for {suite} in a {ws.runners.e2e} container")

    # D15/B17 parity: an app the suite references may not be in the demand (only its worktree
    # apps plus companions are, B9/B19); resolve it to its declared `calls` fallback the same way
    # build.py's build_env() does, so a ref outside the demand never renders a per-demand address
    # that nothing deploys there -- expand() fails the unit naming the key when neither exists.
    in_demand = {u.name for u in demand_apps(ws, demand)[0]}
    fallbacks = app.fallbacks()
    try:
        env = {
            key: expand(tmpl, ws, demand, apps=in_demand, fallbacks=fallbacks)
            for key, tmpl in app.suite_env.items()
        }
    except UsageError as exc:
        return failed(unit, str(exc))
    add_hosts = {
        host(ws, demand, ref): "127.0.0.1"
        for tmpl in app.suite_env.values()
        for ref in url_refs(tmpl)
        if ref in in_demand  # a fallback address is real and outside this demand's namespace
    }

    results_dir = next_run_dir(ws.paths.reports / demand / suite) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    command = ["pytest", "--alluredir=/results"]
    k = ctx.options.get("k")
    if k:
        command += ["-k", k]

    proc = ctx.runner.run(
        ws.runners.e2e,
        mounts=[Mount(suite_dir, f"/w/e2e/{suite}", readonly=True), Mount(results_dir, "/results")],
        env=env, network="host", add_hosts=add_hosts,
        workdir=f"/w/e2e/{suite}", command=command,
    )

    warning = regenerate_report(ws, ctx.runner, ctx.node, demand, suite)
    if proc.returncode != 0:
        reason = f"pytest exit {proc.returncode}"
        return failed(unit, f"{reason}; warning: {warning}" if warning else reason)
    return done(unit, f"warning: {warning}" if warning else "")
