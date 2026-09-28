"""dop env up — shared services (§5)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="env up",
    help="create or update the shared namespace (reports)",
    dimension="shared",
    filters=(),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
