"""dop up — bring a demand into the environment (§5, B10, B18–B20, B26).

The demand's overlay (namespace, wiring, scheduler env, secret — render.py) is rendered and applied
once per demand; every app of the demand (B9 + B19) is then one unit. `deploy` is what copies an
artifact into the node (B6): a workload that has never been deployed has nothing to mount yet and
stays ContainerCreating. That is not a failure here — the unit is done, noting that it waits for
`dop deploy`.
"""

from __future__ import annotations

from collections import defaultdict

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed, planned
from ..render import render_demand
from ..scope import resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="up",
    help="create the demand's namespace, config and workloads",
    dimension="app",
    filters=("tasks", "app"),
    entering=True,
    requires_tasks=True,
)


def _ready(ctx: Context, demand: str, app: str) -> tuple[bool, str]:
    """Whether the app's Deployment has every wanted replica Ready. No Deployment yet (nothing
    scheduled for it) counts as not ready, not an error."""
    ns = namespace(ctx.ws, demand)
    data = ctx.kube.json(["get", "deployment", app, "-n", ns, "--ignore-not-found"])
    if not data:
        return False, "waiting for deploy"
    wanted = data.get("spec", {}).get("replicas", 1) or 0
    ready = data.get("status", {}).get("readyReplicas", 0) or 0
    return ready >= wanted, ("" if ready >= wanted else "waiting for deploy")


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    if ctx.dry_run:
        return planned(unit)
    ok, note = _ready(ctx, unit.demand, unit.name)
    if note:
        # `render()` drops a done unit's reason (B13 lists only non-done units): the note has to
        # be printed to be seen at all, the way `status`/`report` print theirs.
        print(f"{unit.label}: {note}", file=ctx.out)
    return done(unit, note)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)

    by_demand: dict[str, list[Unit]] = defaultdict(list)
    for unit in scope.units:
        by_demand[unit.demand].append(unit)

    for demand, units in by_demand.items():
        try:
            overlay = render_demand(ctx.ws, demand, units)
            ctx.kube.run(["apply", "-k", str(overlay)])
        except Exception as exc:  # render or apply is shared by every app of the demand
            summary.extend(failed(u, f"{type(exc).__name__}: {exc}") for u in units)
            continue
        for unit in units:
            summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
