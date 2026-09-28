"""dop status — readiness and address of every workload (§5, B19, B32)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="status",
    help="readiness and address of every workload",
    dimension="app",
    filters=("tasks", "app"),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
