"""dop report — the reports address of each demand (§5, B23)."""

from ..address import address
from ..context import Context
from ..outcome import Unit, UnitResult, done, skipped
from ..scope import resolve
from . import RunSummary, VerbSpec, act, summary_from

VERB = VerbSpec(
    name="report",
    help="print the reports address",
    dimension="demand",
    filters=("tasks",),
)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda u=unit: _report(ctx, u)))
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
        print(f"{base}/{demand}/{project}/site/", file=ctx.out)
    return done(unit, f"{len(projects)} project(s)")
