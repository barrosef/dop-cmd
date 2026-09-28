"""dop build — build each app's artifact in the demand's own tree (§5, B17, B31)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="build",
    help="build each application's artifact in a host build container",
    dimension="app",
    filters=("tasks", "app"),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
