"""dop down — remove a demand from the environment (§5, B10, B26).

`resolve` (B26) has already kept out anything dop did not label; every unit here is a namespace
dop owns. `down` deletes it and the node directory its artifacts were copied into (B6).
"""

from __future__ import annotations

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, planned
from ..scope import resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="down",
    help="delete the demand's namespace and copied artifacts",
    dimension="demand",
    filters=("tasks",),
    requires_tasks=True,
)


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ns = namespace(ctx.ws, unit.demand)
    ctx.kube.run(["delete", "namespace", ns, "--ignore-not-found"])
    ctx.node.remove(ctx.node.path(ns))
    return planned(unit) if ctx.dry_run else done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
