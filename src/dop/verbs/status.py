"""dop status — readiness and address of every workload (§5, B19, B32).

A cluster that cannot be read fails the unit (via `act`, which turns any exception into a failed
unit) — it is never reported as "down": down is a known state, unreadable is not. Scheduling is an
accepted risk today (B32): no application can switch it off yet, whatever `scheduler_off` config
declares, so every app is shown the same way.
"""

from __future__ import annotations

from ..address import address, namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done
from ..scope import SOURCE_TRUNK, resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="status",
    help="readiness and address of every workload",
    dimension="app",
    filters=("tasks", "app"),
)

_SCHEDULER_NOTE = "scheduler: on (not controllable)"  # B32: true of every app today


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ns = namespace(ctx.ws, unit.demand)
    data = ctx.kube.json(["get", "deployment", unit.name, "-n", ns, "--ignore-not-found"])
    if not data:
        readiness = "not deployed"
    else:
        wanted = data.get("spec", {}).get("replicas", 1) or 0
        ready = data.get("status", {}).get("readyReplicas", 0) or 0
        readiness = f"{ready}/{wanted} ready"

    parts = [readiness, address(ctx.ws, unit.demand, unit.name), _SCHEDULER_NOTE]
    if unit.source == SOURCE_TRUNK:
        parts.append(f"source: trunk ({unit.ref})")
    # `render()` drops a done unit's reason (B13 lists only non-done units), so a status line —
    # unlike a plain pass/fail — is printed here, the way `report` prints its addresses.
    print(f"{unit.label}: {' · '.join(parts)}", file=ctx.out)
    return done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
