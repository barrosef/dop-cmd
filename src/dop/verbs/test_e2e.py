"""dop test e2e — Playwright against the demand's own running apps (§5, B21, B33)."""

from ..context import Context
from . import Option, RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="test e2e",
    help="Playwright suites against the demand's own running applications (headless)",
    dimension="suite",
    filters=("tasks", "app"),
    options=(
        Option(("-k",), {"dest": "k", "metavar": "EXPR", "default": None,
                         "help": "only tests matching EXPR"}),
    ),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
