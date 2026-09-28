"""dop deploy — copy the artifact into the node and serve it (§5, B6, B24)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="deploy",
    help="copy the built artifact into the node and make the workload serve it",
    dimension="app",
    filters=("tasks", "app"),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
