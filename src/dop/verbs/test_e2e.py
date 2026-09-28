"""dop test e2e — Playwright against the demand's own running applications (§5, B21, B33)."""

from ..context import Context
from ..scope import resolve
from . import Option, RunSummary, VerbSpec, act, summary_from
from ._testing import run_suite_unit

VERB = VerbSpec(
    name="test e2e",
    help="Playwright suites against the demand's own running applications (headless)",
    dimension="suite",
    filters=("tasks", "app"),
    options=(
        Option(("-k",), {"dest": "k", "metavar": "EXPR", "default": None, "help": "only tests matching EXPR"}),
    ),
)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda u=unit: run_suite_unit(ctx, u)))
    return summary
