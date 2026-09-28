"""dop log — logs of the scope, following by default (§5)."""

from ..context import Context
from . import Option, RunSummary, VerbSpec, stub

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


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
