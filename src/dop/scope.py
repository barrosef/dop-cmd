"""From filters to units (B8, B9, B11, B12, B13, B19, B26, B28, B29).

Demands present in the environment are the namespaces dop labelled (B11, B26) — the only source.
A demand's code is, per repository, the worktree whose branch carries the key as a token (B8, B28).
A demand's apps are its worktree apps plus their companions (B9, B19). Filters only narrow (B12);
`--tasks` adds demands only on a verb that enters them (`up`).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .address import namespace
from .config import Workspace
from .kube import KubeError
from .outcome import Unit, UnitResult, UsageError, failed, skipped

if TYPE_CHECKING:
    from .context import Context
    from .verbs import VerbSpec

MANAGED_BY = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "dop"
DEMAND_LABEL = "dop/demand"

SOURCE_WORKTREE = "worktree"
SOURCE_TRUNK = "trunk"


class ScopeError(Exception):
    """A repository's worktrees could not be read."""


# ---------------------------------------------------------------------------------------------
# worktrees (B8, B28)


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str | None
    branch: str | None  # short name, without refs/heads/; None when detached
    main: bool  # the repository's main checkout (first entry)
    prunable: bool = False


def worktrees(repo_dir: Path) -> list[Worktree]:
    """`git worktree list --porcelain` of the repository at repo_dir."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "worktree", "list", "--porcelain"],
            capture_output=True, text=True,
        )
    except FileNotFoundError as exc:
        raise ScopeError("git not found on PATH") from exc
    if proc.returncode != 0:
        raise ScopeError(f"git worktree list in {repo_dir}: {(proc.stderr or proc.stdout).strip()}")
    return parse_worktrees(proc.stdout)


def parse_worktrees(porcelain: str) -> list[Worktree]:
    result: list[Worktree] = []
    for block in re.split(r"\n\s*\n", porcelain.strip()):
        if not block.strip():
            continue
        path = head = branch = None
        prunable = False
        for line in block.splitlines():
            word, _, rest = line.partition(" ")
            if word == "worktree":
                path = Path(rest)
            elif word == "HEAD":
                head = rest
            elif word == "branch":
                branch = rest.removeprefix("refs/heads/")
            elif word == "prunable":
                prunable = True
        if path is not None:
            result.append(Worktree(path, head, branch, main=not result, prunable=prunable))
    return result


def branch_matches(key: str, branch: str | None) -> bool:
    """B28: the key, case-insensitively, as a token delimited by start, end, or one of / - _ ."""
    if not branch:
        return False
    pattern = r"(?:^|[/\-_.])" + re.escape(key.lower()) + r"(?:$|[/\-_.])"
    return re.search(pattern, branch.lower()) is not None


class _Worktrees:
    """Per-command cache of each repository's worktree list."""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self._cache: dict[str, list[Worktree] | ScopeError] = {}

    def of(self, repo: str) -> list[Worktree]:
        if repo not in self._cache:
            try:
                self._cache[repo] = worktrees(self.ws.repos[repo].dir)
            except ScopeError as exc:
                self._cache[repo] = exc
        got = self._cache[repo]
        if isinstance(got, ScopeError):
            raise got
        return got

    def for_demand(self, repo: str, demand: str) -> Worktree | None:
        """The demand's worktree of `repo`, None if it has none. ScopeError when it cannot be
        told (git fails, more than one candidate, path gone)."""
        found = [w for w in self.of(repo) if branch_matches(demand, w.branch)]
        if not found:
            return None
        if len(found) > 1:
            names = ", ".join(f"{w.branch} ({w.path})" for w in found)
            raise ScopeError(f"repo {repo}: more than one worktree for {demand}: {names}")
        wt = found[0]
        if not wt.path.is_dir():
            raise ScopeError(f"repo {repo}: worktree of {demand} at {wt.path} no longer exists")
        return wt


def trunk_ref(repo_dir: Path) -> str:
    """B31: a companion's label — the main checkout's actual branch and short sha, plus `dirty`
    when it has local changes. Never the word trunk unchecked."""
    try:
        main = worktrees(repo_dir)[0]
    except (ScopeError, IndexError) as exc:
        return f"unknown ({exc})"
    ref = f"{main.branch or 'detached'}@{(main.head or '?')[:7]}"
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "status", "--porcelain"], capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return f"{ref} (status unknown)"
    return f"{ref} dirty" if proc.stdout.strip() else ref


# ---------------------------------------------------------------------------------------------
# demands present (B11, B26)


def present_demands(ctx: Context) -> list[str]:
    """Demand keys of the namespaces dop labelled. KubeError when the cluster cannot be read."""
    data = ctx.kube.json(["get", "namespaces", "-l", f"{MANAGED_BY}={MANAGED_BY_VALUE}"])
    keys = set()
    for item in data.get("items", []):
        labels = item.get("metadata", {}).get("labels", {}) or {}
        key = labels.get(DEMAND_LABEL)
        if key and re.match(r"^[A-Za-z]+-\d+$", key):
            keys.add(key.upper())
    return sorted(keys)


def namespace_exists(ctx: Context, demand: str) -> bool:
    data = ctx.kube.json(["get", "namespace", namespace(ctx.ws, demand), "--ignore-not-found"])
    return bool(data)


# ---------------------------------------------------------------------------------------------
# the demand's apps (B9, B19)


def demand_apps(ws: Workspace, demand: str, wts: _Worktrees | None = None) -> tuple[list[Unit], list[UnitResult]]:
    """Every app of the demand as an (demand, app) unit, unfiltered, and the results of the apps
    that could not be resolved (failed). Worktree apps first, then companions (source trunk)."""
    wts = wts or _Worktrees(ws)
    units: list[Unit] = []
    problems: list[UnitResult] = []
    for repo in ws.repos:
        repo_apps = ws.apps_of_repo(repo)
        if not repo_apps:
            continue
        try:
            wt = wts.for_demand(repo, demand)
        except ScopeError as exc:
            problems += [failed(Unit("app", demand, a.name, repo=repo), str(exc)) for a in repo_apps]
            continue
        if wt is not None:
            units += [Unit("app", demand, a.name, repo=repo, source=SOURCE_WORKTREE, path=wt.path) for a in repo_apps]

    taken = {u.name for u in units} | {p.unit.name for p in problems}
    for u in list(units):
        for comp in ws.apps[u.name].companions:
            if comp in taken:
                continue
            taken.add(comp)
            repo = ws.apps[comp].repo
            units.append(Unit("app", demand, comp, repo=repo, source=SOURCE_TRUNK, path=ws.repos[repo].dir))
    return units, problems


# ---------------------------------------------------------------------------------------------
# resolve


@dataclass
class Scope:
    """units: what the verb acts on. skipped: results already decided during resolution — skipped
    (out of reach) or failed (could not be resolved) — which the verb passes on to its summary.
    note: said when there was nothing to act on (B11)."""

    units: list[Unit] = field(default_factory=list)
    skipped: list[UnitResult] = field(default_factory=list)
    note: str = ""


def _check_filter(values, known, what: str) -> None:
    unknown = [v for v in values if v not in known]
    if unknown:
        raise UsageError(f"unknown {what}: {', '.join(unknown)}")


def resolve(ctx: Context, verb: VerbSpec) -> Scope:
    ws, f = ctx.ws, ctx.filters
    _check_filter(f.apps, ws.apps, "app")
    _check_filter(f.repos, ws.repos, "repository")

    if verb.dimension == "shared":
        return Scope(units=[Unit("shared", None, ws.address.shared_namespace)])

    if verb.requires_tasks and not f.tasks:
        raise UsageError(f"dop {verb.name} requires --tasks")

    scope = Scope()

    # -- which demands (B11, B12, B26)
    demands: list[str] = []
    if verb.entering:
        candidates = list(dict.fromkeys(f.tasks))
        present: set[str] | None = None
        try:
            present = set(present_demands(ctx))
        except KubeError as exc:
            scope.skipped += [failed(Unit("demand", d), f"cannot read the cluster: {exc}") for d in candidates]
            return scope
    else:
        try:
            present = set(present_demands(ctx))
        except KubeError as exc:
            targets = f.tasks or (None,)
            scope.skipped += [
                failed(Unit("demand", d, None if d else "discovery"), f"cannot read the cluster: {exc}")
                for d in targets
            ]
            return scope
        if not f.tasks:
            candidates = sorted(present)
            if not candidates:
                scope.note = "nothing present in the environment"
                return scope
        else:
            candidates = list(dict.fromkeys(f.tasks))

    for d in candidates:
        if d in present:
            demands.append(d)
            continue
        try:
            exists = namespace_exists(ctx, d)
        except KubeError as exc:
            scope.skipped.append(failed(Unit("demand", d), f"cannot read the cluster: {exc}"))
            continue
        if exists:
            scope.skipped.append(failed(
                Unit("demand", d),
                f"namespace {namespace(ws, d)} exists but is not labelled by dop; not touched (B26)",
            ))
        elif verb.entering:
            demands.append(d)
        else:
            scope.skipped.append(skipped(Unit("demand", d), "not present in the environment"))

    # -- units of each demand
    wts = _Worktrees(ws)
    for d in demands:
        if verb.dimension == "demand":
            scope.units.append(Unit("demand", d))
        elif verb.dimension in ("app", "suite"):
            _app_units(ws, d, verb.dimension, f, wts, scope)
        elif verb.dimension == "repo":
            _repo_units(ws, d, f, wts, scope)
        else:
            raise ValueError(f"unknown dimension {verb.dimension!r}")
    return scope


def _app_units(ws: Workspace, d: str, dimension: str, f, wts: _Worktrees, scope: Scope) -> None:
    units, problems = demand_apps(ws, d, wts)
    if not units and not problems:
        scope.skipped.append(skipped(Unit("demand", d), "no application of the demand has a worktree (B19)"))
        return
    if f.apps:
        units = [u for u in units if u.name in f.apps]
        problems = [p for p in problems if p.unit.name in f.apps]
    units = [
        Unit(u.kind, u.demand, u.name, repo=u.repo, source=u.source, path=u.path,
             ref=trunk_ref(u.path) if u.source == SOURCE_TRUNK else None)
        for u in units
    ]
    if dimension == "app":
        scope.units += units
        scope.skipped += problems
        return
    # suites belong to apps (B13, §5)
    for u in units:
        suite = ws.apps[u.name].suite
        if suite:
            scope.units.append(Unit("suite", d, suite, repo=u.repo, source=u.source, path=u.path, ref=u.ref, app=u.name))
    for p in problems:
        suite = ws.apps[p.unit.name].suite
        if suite:
            scope.skipped.append(UnitResult(Unit("suite", d, suite, repo=p.unit.repo, app=p.unit.name), p.status, p.reason))


def _repo_units(ws: Workspace, d: str, f, wts: _Worktrees, scope: Scope) -> None:
    found_any = False
    for repo in ws.repos:
        try:
            wt = wts.for_demand(repo, d)
        except ScopeError as exc:
            found_any = True
            if not f.repos or repo in f.repos:
                scope.skipped.append(failed(Unit("repo", d, repo, repo=repo), str(exc)))
            continue
        if wt is None:
            continue
        found_any = True
        if not f.repos or repo in f.repos:
            scope.units.append(Unit("repo", d, repo, repo=repo, source=SOURCE_WORKTREE, path=wt.path))
    if not found_any:
        scope.skipped.append(skipped(Unit("demand", d), "no repository has a worktree for the demand"))
