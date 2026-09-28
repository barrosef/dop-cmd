"""dop deploy — copy the built artifact into the node and serve it (§5, B6, B24).

The artifact is the one `build` left (build.build_dir / artifact_dir — deterministic, so deploy
never needs to be told where it is). `Node.sync` stages it in the node and only then replaces the
mounted directory's contents (B16, B24); an absent or empty artifact raises before anything in the
node is touched, which fails the unit and replaces nothing. A back-end unit is done only once its
rollout has restarted and gone Ready; a front-end unit is done once the files are in place — nothing
in the cluster needs telling once a hostPath's contents changed under it.
"""

from __future__ import annotations

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, planned
from ..scope import resolve
from . import VerbSpec, act, summary_from
from .build import artifact_dir

VERB = VerbSpec(
    name="deploy",
    help="copy the built artifact into the node and make the workload serve it",
    dimension="app",
    filters=("tasks", "app"),
)

# Not fixed by business-rules.md or spec.md: how long a rollout may take before deploy gives up.
_ROLLOUT_TIMEOUT = "180s"


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ws = ctx.ws
    app = ws.apps[unit.name]
    artifact = artifact_dir(ws, app, unit)
    ns = namespace(ws, unit.demand)
    dest = ctx.node.path(ns, unit.name)

    ctx.node.sync(artifact, dest)

    if app.kind == "backend":
        ctx.kube.run(["-n", ns, "rollout", "restart", f"deployment/{unit.name}"])
        ctx.kube.run(["-n", ns, "rollout", "status", f"deployment/{unit.name}",
                       f"--timeout={_ROLLOUT_TIMEOUT}"])

    if ctx.dry_run:
        return planned(unit, f"would deploy {artifact} to {dest}")
    return done(unit, f"deployed {artifact} to {dest}")


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
