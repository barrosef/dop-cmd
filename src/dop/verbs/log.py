"""dop log — logs of the scope, following by default (§5).

Following several units at once would interleave them unreadably, so `dop log` only streams
(`kubectl logs -f`) when exactly one unit is in scope — narrow with `--tasks`/`--app` to get there.
More than one unit, still wanting to follow: every unit is listed, none acted on (exit 3), rather
than guessing which one the operator meant. `--no-follow` instead dumps each one's logs in turn,
prefixed by its label.
"""

from __future__ import annotations

from ..address import namespace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, skipped
from ..scope import resolve
from . import Option, VerbSpec, act, summary_from

VERB = VerbSpec(
    name="log",
    help="logs of the scope; follows by default",
    dimension="app",
    filters=("tasks", "app"),
    options=(
        Option(("--no-follow",), {"dest": "follow", "action": "store_false", "default": True,
                                   "help": "print what is there and stop"}),
    ),
)


def _run_one(ctx: Context, unit: Unit, follow: bool) -> UnitResult:
    ns = namespace(ctx.ws, unit.demand)
    print(f"==> {unit.label} <==", file=ctx.out)
    args = ["logs", "-n", ns, f"deployment/{unit.name}"]
    if follow:
        args.append("-f")
    # Reading logs changes nothing, so it runs the same in dry-run (Kube.run's `read=True`, the
    # same gate `present_demands` uses).
    ctx.kube.run(args, read=True, capture=False)
    return done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    follow = ctx.options.get("follow", True)
    if follow and len(scope.units) > 1:
        for unit in scope.units:
            summary.add(skipped(
                unit, "several units match; narrow with --tasks/--app to follow one, "
                "or pass --no-follow to dump them all",
            ))
        return summary
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit, follow)))
    return summary
