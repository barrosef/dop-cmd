"""dop up — bring a demand into the environment (§5, B10, B18–B20, B26, B37–B39).

The demand's overlay (namespace, wiring, scheduler env, secret — render.py) is rendered and applied
once per demand, always from every app of the demand (B37): `--app` narrows which workloads are
applied, never what the config says. Every app acted on is then one unit. `deploy` is what copies an
artifact into the node (B6): a workload that has never been deployed has nothing to mount yet and
stays ContainerCreating. That is not a failure here — the unit is done, noting that it waits for
`dop deploy`.

Dry-run renders nothing and writes nothing (B38): the overlay is computed — so what would fail
still fails — and the apply printed. On a real run the secret file exists only while the apply
runs (B39).

B18 amended (callee running): config still renders every key as if the whole demand were up
(B37), but `--app` narrows what actually gets applied — so an applied app's key can point at a
callee that is part of the demand yet has no Deployment at all, because it was never brought up
here or in an earlier `--app` run. That is a warning on the calling app's unit, not a failure:
`status` is what verifies wiring against the live cluster.
"""

from __future__ import annotations

from collections import defaultdict

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed, planned
from ..render import apply_selector, check_rendered, overlay_dir, plan_demand, rendered
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


def _wiring_notes(
    ctx: Context, unit: Unit, demand_names: frozenset[str], applied_names: frozenset[str]
) -> list[str]:
    """B18 amended: for each of this app's calls whose callee is part of the demand, a note when
    that callee is not actually up — a dry-run cannot know that (nothing was applied), so it warns
    instead that the callee is left out of *this* apply; a real run checks the live Deployment.
    A callee outside the demand (its fallback is used) is none of this unit's concern."""
    app = ctx.ws.apps[unit.name]
    notes = []
    for c in app.calls:
        if c.app not in demand_names:
            continue
        if ctx.dry_run:
            if c.app not in applied_names:
                notes.append(f"would point at local {c.app}, not in this apply")
            continue
        ns = namespace(ctx.ws, unit.demand)
        data = ctx.kube.json(["get", "deployment", c.app, "-n", ns, "--ignore-not-found"])
        if not data:
            notes.append(
                f"{c.key} points at local {c.app}, which is not up — "
                f"run dop up --tasks {unit.demand} --app {c.app} (or without --app)"
            )
    return notes


def _run_one(
    ctx: Context, unit: Unit,
    demand_names: frozenset[str] = frozenset(), applied_names: frozenset[str] = frozenset(),
) -> UnitResult:
    for note in _wiring_notes(ctx, unit, demand_names, applied_names):
        # Same reasoning as the "waiting for deploy" note below: a warning on a done unit is only
        # seen here, since `render()` drops a done unit's reason (B13).
        print(f"{unit.label}: {note}", file=ctx.out)
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
        every_app = scope.demand_apps.get(demand, units)
        demand_names = frozenset(u.name for u in every_app)
        applied_names = frozenset(u.name for u in units)
        selector = apply_selector([u.name for u in every_app], [u.name for u in units])
        try:  # render and apply are shared by every app of the demand
            if ctx.dry_run:
                plan_demand(ctx.ws, demand, every_app)
                ctx.kube.run(["apply", "-k", str(overlay_dir(ctx.ws, demand)), *selector])
            else:
                with rendered(ctx.ws, demand, every_app) as overlay:
                    check_rendered(ctx.kube.run(["kustomize", str(overlay)], read=True).stdout)
                    ctx.kube.run(["apply", "-k", str(overlay), *selector])
        except Exception as exc:
            summary.extend(failed(u, f"{type(exc).__name__}: {exc}") for u in units)
            continue
        for unit in units:
            summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit, demand_names, applied_names)))
    return summary
