"""dop env up — create or update the shared namespace (§5, B4).

Applies the workspace's shared manifests (`<paths.manifests>/shared`) as one kustomization, and
seeds the node directory the shared `reports` service serves (B23) so it exists even before any
report has been published. Node has no bare "create a directory": a small placeholder page is
synced in the same way `deploy` stages real artifacts (B6, B16, B24) — harmless to redo, so `env up`
stays idempotent.
"""

from __future__ import annotations

from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, planned
from ..scope import resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="env up",
    help="create or update the shared namespace (reports)",
    dimension="shared",
    filters=(),
)

_REPORTS_DIR = "reports"
_PLACEHOLDER = "<!doctype html><title>dop reports</title><p>no report published yet.\n"


def _seed_reports_dir(ctx: Context) -> None:
    staging = ctx.ws.state_dir / "seed" / _REPORTS_DIR
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "index.html").write_text(_PLACEHOLDER, encoding="utf-8")
    ctx.node.sync(staging, ctx.node.path(_REPORTS_DIR))


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    manifests = ctx.ws.paths.manifests / "shared"
    ctx.kube.run(["apply", "-k", str(manifests)])
    _seed_reports_dir(ctx)
    return planned(unit) if ctx.dry_run else done(unit)


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
