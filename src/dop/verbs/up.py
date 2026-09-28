"""dop up — bring demands into the environment (§5, B10)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="up",
    help="create the demand's namespace, config and workloads",
    dimension="app",
    filters=("tasks", "app"),
    entering=True,
    requires_tasks=True,
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
