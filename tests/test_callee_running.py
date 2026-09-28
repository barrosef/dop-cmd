"""dop up: B18 amended (callee running).

`be` calls `api` (config.CONFIG's `[apps.be].calls`, key API_URL); both have their own worktree in
the fixture demand, so both are part of it (B9/B37) regardless of `--app`. The real fake kubectl
answers `get deployment` not-found for any name it was not told about (see tests/fakes/kubectl and
tests/test_verbs_2a.py's own note on it) — exactly "never brought up" for `api` when `--app`
excludes it.
"""

from __future__ import annotations

from dop.outcome import Status, Unit
from dop.verbs import up

from .conftest import make_ctx, run_cli, worktree_dir


def _seed_manifests(ws, *apps: str) -> None:
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        app = ws.apps[name]
        (ws.paths.manifests / "demand" / kind_dir[app.kind] / name).mkdir(parents=True, exist_ok=True)


def _k1_with_api_and_be(root, fake, ws):
    fake.present()  # entering: up must create the namespace regardless
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    _seed_manifests(ws, "api", "be", "fe")  # fe: be's companion (B19), pulled in too
    return root


def test_up_warns_when_narrowed_apply_leaves_the_callee_out(root, fake, ws):
    """`--app be` excludes `api` from what is applied; `api`'s Deployment (the real fake's
    default: not found) means `be`'s own key would point at a callee that is not up."""
    root = _k1_with_api_and_be(root, fake, ws)
    code, out = run_cli(root, "up", "--tasks", "K-1", "--app", "be")
    assert code == 0, out  # a warning, not a failure
    assert ("K-1/be: API_URL points at local api, which is not up — "
            "run dop up --tasks K-1 --app api (or without --app)") in out


def test_up_dry_run_says_would_point_at_local_not_in_this_apply(root, fake, ws):
    root = _k1_with_api_and_be(root, fake, ws)
    code, out = run_cli(root, "up", "--tasks", "K-1", "--app", "be", "--dry-run")
    assert code == 0, out
    assert "K-1/be: would point at local api, not in this apply" in out


def test_up_no_warning_when_the_callee_is_applied_too(root, fake, ws):
    """No `--app`: both `api` and `be` are applied together, so `api` is in `applied_names` and
    the dry-run branch of the warning does not fire; a real run still depends on the live
    Deployment (exercised directly below, since the shared fake only ever answers "not found")."""
    root = _k1_with_api_and_be(root, fake, ws)
    code, out = run_cli(root, "up", "--tasks", "K-1", "--dry-run")
    assert code == 0, out
    assert "would point at local api" not in out


def test_up_no_warning_when_the_callee_is_actually_up(ws):
    """Direct `_run_one` call with a stand-in `Kube` that answers `api`'s Deployment as present —
    the same style test_verbs_2a.py uses for the ready/not-ready readiness branch."""

    class ReadyKube:
        def json(self, args):
            return {"spec": {"replicas": 1}, "status": {"readyReplicas": 1}}

    ctx = make_ctx(ws, tasks=["K-1"], apps=["be"])
    ctx.kube = ReadyKube()
    result = up._run_one(ctx, Unit("app", "K-1", "be"), frozenset({"api", "be"}), frozenset({"be"}))
    assert result.status is Status.DONE
    assert result.reason == ""  # `_ready` is the only thing that would set a reason here


def test_up_no_warning_for_a_call_outside_the_demand(ws):
    """`api` not part of the demand at all (its config fallback would be used instead, per B18):
    none of this is `be`'s or `up`'s concern, so no live check for it — its own readiness check
    (`get deployment be`) still runs, answering "not found" like the shared fake's default."""

    class ExplodingKube:
        def json(self, args):
            assert args[2] != "api", "must not query a callee that is not part of the demand"
            return {}

    ctx = make_ctx(ws, tasks=["K-1"], apps=["be"])
    ctx.kube = ExplodingKube()
    result = up._run_one(ctx, Unit("app", "K-1", "be"), frozenset({"be", "fe"}), frozenset({"be"}))
    assert result.status is Status.DONE


def test_up_default_arguments_keep_old_callers_unaffected(ws, fake):
    """`_run_one(ctx, unit)` with no demand/applied names (test_verbs_2a.py's own calls) never
    triggers a wiring warning: it behaves exactly as before this feature existed."""
    ctx = make_ctx(ws, tasks=["K-1"])
    result = up._run_one(ctx, Unit("app", "K-1", "be"))
    assert result.status is Status.DONE
    assert result.reason == "waiting for deploy"  # the fake answers "not found" for be itself too
