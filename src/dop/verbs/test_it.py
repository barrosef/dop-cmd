"""dop test it — integration tests on the demand's worktree (§5, B22, B29, B35)."""

from ..context import Context
from ..scope import resolve
from . import RunSummary, VerbSpec, act, summary_from
from ._testing import run_repo_layer

VERB = VerbSpec(
    name="test it",
    help="integration tests on the demand's worktree, in a host runner container",
    dimension="repo",
    filters=("tasks", "repo"),
)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda u=unit: run_repo_layer(ctx, u, "it")))
    return summary
