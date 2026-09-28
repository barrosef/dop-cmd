"""QA F6 — lock granularity across `test aaa` / `test it` on the same repo (B13, B22, B27).

Oracle: docs/business-rules.md B13 (units: (demand, repo) for `test aaa|it`), B22 (each (demand,
repo) *test project* is staged to `<layer>-<repo>`, so aaa and it never share a build directory),
B27 (one writer per unit, a second command on the same unit fails naming the holder).
Scope: FRONT (test report verbs) — src/dop/lock.py, src/dop/verbs/test_aaa.py, test_it.py.
Pure unit test against dop.lock; no fakes, no filesystem outside tmp_path needed.
"""

from __future__ import annotations

from dataclasses import replace
from dop.context import Context, Filters
from dop.lock import LockHeld, lock, lock_name
from dop.outcome import Status, Unit, done
from dop.verbs import act


def test_aaa_and_it_units_of_the_same_repo_share_one_lock_name():
    """`scope._repo_units` (shared by both test_aaa.py and test_it.py's `resolve(ctx, VERB)`)
    builds `Unit("repo", demand, repo, repo=repo, ...)` with no mention anywhere of which layer is
    asking. `lock_name` keys off `(unit.kind, unit.demand, unit.name)` only -- so the "aaa" unit
    and the "it" unit of the same (demand, repo) are, for locking purposes, indistinguishable."""
    unit_aaa = Unit("repo", "K-1", "api", repo="api")
    unit_it = Unit("repo", "K-1", "api", repo="api")
    assert lock_name(unit_aaa) == lock_name(unit_it) == "repo__K-1__api.lock"


def test_test_it_is_refused_while_test_aaa_holds_the_repo_lock(ws):
    # amended v4 (B27 amended): the unit identity now includes the verb family, folded in only at
    # the *lock's* identity by `verbs.act(..., family=...)` (never into `Unit` itself) -- exactly
    # what test_aaa.py/test_it.py call. Going through raw `lock()` on two identical `Unit`s (as
    # this test used to) bypasses that mechanism and re-tests the pre-v4 defect it fixed. Now goes
    # through `act()` the way the real verbs do, architect 28/09
    """`test aaa --repo api --tasks K-1` and `test it --repo api --tasks K-1` are different real
    operations on disjoint state: B22 stages them to `.dop/runs/K-1/aaa-api/` and
    `.dop/runs/K-1/it-api/` respectively, and B23 reports them under the distinct projects
    `aaa-api` and `it-api`. Nothing about running one interferes with the other: `test it` must
    not be refused while an unrelated `test aaa` run is merely in progress on the same repo.
    """
    import io

    unit_aaa = Unit("repo", "K-1", "api", repo="api")
    unit_it = Unit("repo", "K-1", "api", repo="api")
    ctx_aaa = Context(ws=ws, filters=Filters(), dry_run=False, out=io.StringIO(),
                       command="dop test aaa --repo api --tasks K-1")
    ctx_it = Context(ws=ws, filters=Filters(), dry_run=False, out=io.StringIO(),
                      command="dop test it --repo api --tasks K-1")

    result_it = None

    def fn_aaa():
        nonlocal result_it
        # while aaa's own lock is held (act's `with`), a concurrent `test it` on the same repo
        # must go through -- it is a different family, hence a different lock name.
        result_it = act(ctx_it, unit_it, lambda: done(unit_it), family="it")
        return done(unit_aaa)

    result_aaa = act(ctx_aaa, unit_aaa, fn_aaa, family="aaa")

    assert result_aaa.status == Status.DONE, result_aaa
    assert result_it is not None and result_it.status == Status.DONE, (
        f"`dop test it --repo api --tasks K-1` was refused ({result_it}) while "
        "`dop test aaa --repo api --tasks K-1` held the lock, even though the two commands write "
        "to different staging directories (.dop/runs/K-1/aaa-api vs .dop/runs/K-1/it-api, B22) "
        "and different report projects (aaa-api vs it-api, B23)."
    )
