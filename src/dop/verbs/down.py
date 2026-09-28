"""dop down — remove a demand from the environment (§5, B10, B26, B27, B40).

`resolve` has already kept out every demand whose namespace is not dop's (B26, B40); every unit
here is a namespace dop owns. `down` deletes it by its labels — never by the name it computes to
(B40) — and the node directory its artifacts were copied into (B6). It holds every app lock of the
demand while doing so, so no writer acts on an app it removes (B27).
"""

from __future__ import annotations

from contextlib import ExitStack

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, planned
from ..scope import demand_selector, resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="down",
    help="delete the demand's namespace and copied artifacts",
    dimension="demand",
    filters=("tasks",),
    requires_tasks=True,
)


def _run_one(ctx: Context, unit: Unit, label: str) -> UnitResult:
    with ExitStack() as locks:
        # Every app the workspace knows, not only those with a worktree today: an app deployed
        # earlier is still in the namespace this removes.
        for app in ctx.ws.apps:
            locks.enter_context(ctx.lock(Unit("app", unit.demand, app)))
        ns = namespace(ctx.ws, unit.demand)
        ctx.kube.run(["delete", "namespace", "-l", demand_selector(label)])
        ctx.node.remove(ctx.node.path(ns))
    return planned(unit) if ctx.dry_run else done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        label = scope.labels.get(unit.demand, unit.demand)
        summary.add(act(ctx, unit, lambda unit=unit, label=label: _run_one(ctx, unit, label)))
    return summary
