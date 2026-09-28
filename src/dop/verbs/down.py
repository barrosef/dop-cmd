"""dop down — remove demands from the environment (§5, B10, B26)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="down",
    help="delete the demand's namespace and copied artifacts",
    dimension="demand",
    filters=("tasks",),
    requires_tasks=True,
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
