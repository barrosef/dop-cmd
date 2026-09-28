"""dop build — build each app's artifact in the demand's own tree (§5, B17, B31).

A worktree app builds in place, in its own worktree (unique to the demand — no two demands ever
share it). A companion (B19) is copied from the main checkout into
`<workspace>/.dop/builds/<DEMAND>/<app>/` and built there (B31), so a companion build never touches
the checkout other demands read. Either way the build runs in a host container (B21): the build
directory mounted, `build.image`/`build.command` run in it. Back-ends get a shared Maven cache
volume; a front-end's `node_modules` simply stays inside the directory that was built (no cache).

Front-end `build.env` is expanded to the demand's address of the apps it calls, or their declared
fallback when an app is outside the demand (B17). After a front-end builds, its artifact is scanned
for every string `forbidden` names; a hit fails the unit, naming the file and the string.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..address import expand
from ..config import App, Workspace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed, planned
from ..runner import Mount
from ..scope import SOURCE_TRUNK, demand_apps, resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="build",
    help="build each application's artifact in a host build container",
    dimension="app",
    filters=("tasks", "app"),
)

# Not fixed by business-rules.md or spec.md: a shared cache keeps every backend's Maven downloads
# across demands (the build output itself never is), and /work is just where each build runs.
_MAVEN_CACHE_VOLUME = "dop-maven-cache"
_MAVEN_CACHE_PATH = "/root/.m2"
_WORKDIR = "/work"

_EXCLUDED_FROM_COPY = ("target", "dist", "node_modules", ".git")


def build_dir(ws: Workspace, unit: Unit) -> Path:
    """Where a unit's build runs (B31): the worktree itself, or the companion's own copy."""
    if unit.source == SOURCE_TRUNK:
        return ws.state_dir / "builds" / unit.demand / unit.name
    return unit.path


def artifact_dir(ws: Workspace, app: App, unit: Unit) -> Path:
    """Where the built artifact lands — deploy locates it the same way (B31)."""
    return build_dir(ws, unit) / app.artifact


def _copy_checkout(src: Path, dest: Path) -> None:
    """A companion's build source: the main checkout, without target/dist/node_modules/.git."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*_EXCLUDED_FROM_COPY))


def build_env(ws: Workspace, app: App, unit: Unit) -> dict[str, str]:
    """Front-end build.env, expanded to the demand's address of each app it calls, or that app's
    declared fallback when it is outside the demand (B17)."""
    in_demand_units, _ = demand_apps(ws, unit.demand)
    in_demand = {u.name for u in in_demand_units}
    fallbacks = app.fallbacks()
    return {key: expand(tmpl, ws, unit.demand, apps=in_demand, fallbacks=fallbacks)
            for key, tmpl in app.build.env.items()}


def _scan_forbidden(app: App, artifact: Path) -> str | None:
    """The first forbidden string found in the artifact, naming the file (B17)."""
    for path in sorted(p for p in artifact.rglob("*") if p.is_file()):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for needle in app.forbidden:
            if needle.encode("utf-8") in data:
                return f"{path.relative_to(artifact)}: contains forbidden string {needle!r}"
    return None


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ws = ctx.ws
    app = ws.apps[unit.name]
    dest = build_dir(ws, unit)

    if unit.source == SOURCE_TRUNK:
        if ctx.dry_run:
            print(f"[dry-run] copy {unit.path} -> {dest} (excluding {', '.join(_EXCLUDED_FROM_COPY)})",
                  file=ctx.out)
        else:
            _copy_checkout(unit.path, dest)

    env = build_env(ws, app, unit) if app.kind == "frontend" else {}
    mounts = [Mount(dest, _WORKDIR)]
    if app.kind == "backend":
        mounts.append(Mount(_MAVEN_CACHE_VOLUME, _MAVEN_CACHE_PATH))

    proc = ctx.runner.run(
        app.build.image, mounts=mounts, env=env, workdir=_WORKDIR, command=app.build.command,
        capture=True,
    )

    if ctx.dry_run:
        return planned(unit, f"would build in {dest}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return failed(unit, f"build failed (exit {proc.returncode})" + (f": {detail}" if detail else ""))

    artifact = artifact_dir(ws, app, unit)
    if app.kind == "frontend":
        hit = _scan_forbidden(app, artifact)
        if hit:
            return failed(unit, hit)

    return done(unit, f"built in {artifact}")


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
