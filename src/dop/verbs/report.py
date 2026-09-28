"""dop report — the reports address of each demand (§5, B23)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="report",
    help="print the reports address",
    dimension="demand",
    filters=("tasks",),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
