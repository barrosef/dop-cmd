"""dop test it — integration tests on the demand's worktree (§5, B22, B29, B35)."""

from ..context import Context
from . import RunSummary, VerbSpec, stub

VERB = VerbSpec(
    name="test it",
    help="integration tests on the demand's worktree, in a host runner container",
    dimension="repo",
    filters=("tasks", "repo"),
)


def run(ctx: Context) -> RunSummary:
    return stub(ctx, VERB)
