"""dop status — readiness and address of every workload (§5, B19, B32).

A cluster that cannot be read fails the unit (via `act`, which turns any exception into a failed
unit) — it is never reported as "down": down is a known state, unreadable is not. Scheduling is an
accepted risk today (B32): no application can switch it off yet, whatever `scheduler_off` config
declares, so every app is shown the same way. It reads only, so it takes no lock (B27 amended).

Wiring: an app with `calls` gets one line per key, the value resolved the same way `render`
resolves it for the `optum-urls` ConfigMap — a callee in the demand gets its in-namespace address,
one outside it gets its configured fallback. `--wiring` prints only those lines (no readiness, no
address). Unless the run is a dry-run, each key is also checked against the live ConfigMap: a
value that does not match, or a ConfigMap that is not there at all, fails the unit rather than
just reporting a number nobody asked to be believed.
"""

from __future__ import annotations

from ..address import address, namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed
from ..render import URLS_CONFIGMAP
from ..scope import SOURCE_TRUNK, resolve
from . import Option, VerbSpec, read, summary_from

VERB = VerbSpec(
    name="status",
    help="readiness and address of every workload",
    dimension="app",
    filters=("tasks", "app"),
    options=(
        Option(("--wiring",), {"action": "store_true", "default": False,
                                "help": "print only each app's calls, verified against the live ConfigMap"}),
    ),
)

_SCHEDULER_NOTE = "scheduler: on (not controllable)"  # B32: true of every app today


def _wiring(ctx: Context, unit: Unit, names: set[str]) -> tuple[list[str], str | None]:
    """This app's own `calls`, one line per key — the same resolution `render` gives the
    `optum-urls` ConfigMap (`address()` for a callee in the demand, else its fallback). `names` is
    the demand's whole app set, unfiltered by `--app` (`Scope.demand_apps`), the same set `render`
    resolves against.

    Returns the lines to print and a failure reason (None when there is none). A dry-run prints
    the expectation only — no live ConfigMap, no drift claimed against a demand that may not be up."""
    app = ctx.ws.apps[unit.name]
    if not app.calls:
        return [], None
    expected = {c.key: address(ctx.ws, unit.demand, c.app, c.form) if c.app in names else c.fallback
                for c in app.calls}
    lines = [
        f"{c.key} -> {expected[c.key]} "
        + (f"[local {c.app}]" if c.app in names else f"[remote {c.app}: not in the demand]")
        for c in app.calls
    ]
    if ctx.dry_run:
        return lines, None
    ns = namespace(ctx.ws, unit.demand)
    data = ctx.kube.json(["get", "configmap", URLS_CONFIGMAP, "-n", ns, "--ignore-not-found"])
    if not data:
        return lines, f"{URLS_CONFIGMAP} ConfigMap not found in {ns} (run dop up --tasks {unit.demand})"
    live = data.get("data", {}) or {}
    drift = [k for k in expected if live.get(k) != expected[k]]
    if drift:
        reason = "; ".join(
            f"wiring drift: KEY {k}, expected {expected[k]} (run dop up --tasks {unit.demand})" for k in drift
        )
        return lines, reason
    return lines, None


def _run_one(ctx: Context, unit: Unit, names: set[str]) -> UnitResult:
    wiring_only = ctx.options.get("wiring", False)
    if not wiring_only:
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

    lines, reason = _wiring(ctx, unit, names)
    for line in lines:
        print(f"{unit.label}: {line}" if wiring_only else f"  {line}", file=ctx.out)
    if reason:
        return failed(unit, reason)
    return done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        names = {u.name for u in scope.demand_apps.get(unit.demand, ())}
        summary.add(read(ctx, unit, lambda unit=unit, names=names: _run_one(ctx, unit, names)))
    return summary
