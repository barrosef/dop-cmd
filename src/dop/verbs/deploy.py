"""dop deploy — copy the built artifact into the node and serve it (§5, B6, B24).

The artifact is the one `build` left (build.build_dir / artifact_dir — deterministic, so deploy
never needs to be told where it is). It is validated on the host first (B41): a back-end artifact
holds exactly one runnable jar, a front-end one an index.html. `Node.sync` then stages it in the
node, checks it again there, and only then replaces the mounted directory's contents (B16, B24);
an absent, empty or invalid artifact fails the unit before anything in the node is touched. A
back-end unit is done only once its rollout has restarted and gone Ready; a front-end unit is done
once the files are in place — nothing in the cluster needs telling once a hostPath's contents
changed under it.
"""

from __future__ import annotations

from pathlib import Path

from ..address import namespace
from ..config import App
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed, planned
from ..scope import resolve
from . import VerbSpec, act, summary_from
from .build import artifact_dir

VERB = VerbSpec(
    name="deploy",
    help="copy the built artifact into the node and make the workload serve it",
    dimension="app",
    filters=("tasks", "app"),
)

# Not fixed by business-rules.md or spec.md: how long a rollout may take before deploy gives up.
_ROLLOUT_TIMEOUT = "180s"


# Jars a Maven build leaves next to the runnable one; the workload's start script skips the same
# two when it resolves /app/artifact/*.jar.
_NOT_RUNNABLE = ("-sources.jar", "-javadoc.jar")


def _invalid(app: App, artifact: Path) -> str | None:
    """Why the artifact cannot be served, or None (B41). Absent or empty is left to Node.sync."""
    if not artifact.is_dir() or not any(artifact.iterdir()):
        return None
    if app.kind == "backend":
        jars = sorted(p.name for p in artifact.glob("*.jar")
                      if p.is_file() and not p.name.endswith(_NOT_RUNNABLE))
        if len(jars) != 1:
            found = ", ".join(jars) if jars else "none"
            return f"{artifact}: needs exactly one runnable jar, found {found}; nothing replaced"
    elif not (artifact / "index.html").is_file():
        return f"{artifact}: no index.html; nothing replaced"
    return None


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ws = ctx.ws
    app = ws.apps[unit.name]
    artifact = artifact_dir(ws, app, unit)
    ns = namespace(ws, unit.demand)
    dest = ctx.node.path(ns, unit.name)

    reason = _invalid(app, artifact)
    if reason:
        return failed(unit, reason)
    ctx.node.sync(artifact, dest)

    if app.kind == "backend":
        ctx.kube.run(["-n", ns, "rollout", "restart", f"deployment/{unit.name}"])
        ctx.kube.run(["-n", ns, "rollout", "status", f"deployment/{unit.name}",
                       f"--timeout={_ROLLOUT_TIMEOUT}"])

    if ctx.dry_run:
        return planned(unit, f"would deploy {artifact} to {dest}")
    return done(unit, f"deployed {artifact} to {dest}")


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
