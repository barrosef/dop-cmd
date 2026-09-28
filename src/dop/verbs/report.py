"""dop report — the reports address of each demand, or publishing existing report sites into the
shared `reports` service (§5, B23, B44).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..address import address
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, planned, skipped
from ..scope import resolve
from . import Option, VerbSpec, read, summary_from

VERB = VerbSpec(
    name="report",
    help="print the reports address, or publish generated report sites (--publish)",
    dimension="demand",
    filters=("tasks",),
    options=(
        Option(("--publish",), {
            "action": "store_true", "default": False,
            "help": "publish existing report sites into the shared reports service",
        }),
    ),
)


def run(ctx: Context) -> RunSummary:
    if ctx.options.get("publish"):
        return _run_publish(ctx)
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(read(ctx, unit, lambda u=unit: _report(ctx, u)))
    return summary


def _report(ctx: Context, unit: Unit) -> UnitResult:
    demand = unit.demand
    demand_dir = ctx.ws.paths.reports / demand
    projects = (
        sorted(p.name for p in demand_dir.iterdir() if (p / "site").is_dir())
        if demand_dir.is_dir() else []
    )
    if not projects:
        return skipped(unit, "no report generated yet")
    base = address(ctx.ws, None, "reports")
    for project in projects:
        print(f"{base}/{demand}/{project}/", file=ctx.out)
    return done(unit, f"{len(projects)} project(s)")


# ---------------------------------------------------------------------------------------------
# --publish (B44): copy already-generated report sites into the node's shared reports directory.
#
# Two kinds of unit, resolved by scanning the workspace's report root directly -- this is not a
# demand-scoped command (raw per-demand results have no place in it): a top-level directory that
# holds its own `index.html` is a published project site (`aaa-<repo>`, `it-<repo>`, `e2e-<suite>`)
# and gets `Node.sync`ed whole into its own name under the node; the hand-written landing page --
# whatever plain files sit at the report root beside those directories (its `index.html`, the
# screenshots it references) -- is staged on its own and `Node.put` lays it down overwriting only
# files of the same name. Each unit succeeds or fails independently; a raw demand directory (no
# `index.html` of its own) is never touched.


def _publish_scope(reports_root: Path) -> tuple[list[Path], list[Path]]:
    if not reports_root.is_dir():
        return [], []
    entries = sorted(reports_root.iterdir(), key=lambda p: p.name)
    projects = [p for p in entries if p.is_dir() and (p / "index.html").is_file()]
    files = [p for p in entries if p.is_file()]
    return projects, files


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _publish_project(ctx: Context, project: Path) -> UnitResult:
    unit = Unit("report", None, project.name)
    dest = ctx.node.path(ctx.ws.address.shared_namespace, "reports", project.name)
    size = _dir_size(project)
    if ctx.dry_run:
        return planned(unit, f"would publish {project} ({size} bytes) to {dest}")
    ctx.node.sync(project, dest)
    return done(unit, f"published {project} ({size} bytes) to {dest}")


def _publish_landing(ctx: Context, files: list[Path]) -> UnitResult:
    unit = Unit("report", None, "landing")
    dest = ctx.node.path(ctx.ws.address.shared_namespace, "reports")
    names = ", ".join(f.name for f in files)
    size = sum(f.stat().st_size for f in files)
    if ctx.dry_run:
        return planned(unit, f"would publish {names} ({size} bytes) to {dest}")
    staging = ctx.ws.state_dir / "publish" / "landing"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for f in files:
        shutil.copy2(f, staging / f.name)
    ctx.node.put(staging, dest)
    return done(unit, f"published {names} ({size} bytes) to {dest}")


def _run_publish(ctx: Context) -> RunSummary:
    projects, files = _publish_scope(ctx.ws.paths.reports)
    summary = RunSummary()
    if not projects and not files:
        summary.note = "no report site to publish"
        return summary
    for project in projects:
        summary.add(read(ctx, Unit("report", None, project.name), lambda p=project: _publish_project(ctx, p)))
    if files:
        summary.add(read(ctx, Unit("report", None, "landing"), lambda: _publish_landing(ctx, files)))
    return summary
