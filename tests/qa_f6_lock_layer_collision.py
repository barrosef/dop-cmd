"""QA F6 — lock granularity across `test aaa` / `test it` on the same repo (B13, B22, B27).

Oracle: docs/business-rules.md B13 (units: (demand, repo) for `test aaa|it`), B22 (each (demand,
repo) *test project* is staged to `<layer>-<repo>`, so aaa and it never share a build directory),
B27 (one writer per unit, a second command on the same unit fails naming the holder).
Scope: FRONT (test report verbs) — src/dop/lock.py, src/dop/verbs/test_aaa.py, test_it.py.
Pure unit test against dop.lock; no fakes, no filesystem outside tmp_path needed.
"""

from __future__ import annotations

from dop.lock import LockHeld, lock, lock_name
from dop.outcome import Unit


def test_aaa_and_it_units_of_the_same_repo_share_one_lock_name():
    """`scope._repo_units` (shared by both test_aaa.py and test_it.py's `resolve(ctx, VERB)`)
    builds `Unit("repo", demand, repo, repo=repo, ...)` with no mention anywhere of which layer is
    asking. `lock_name` keys off `(unit.kind, unit.demand, unit.name)` only -- so the "aaa" unit
    and the "it" unit of the same (demand, repo) are, for locking purposes, indistinguishable."""
    unit_aaa = Unit("repo", "K-1", "api", repo="api")
    unit_it = Unit("repo", "K-1", "api", repo="api")
    assert lock_name(unit_aaa) == lock_name(unit_it) == "repo__K-1__api.lock"


def test_test_it_is_refused_while_test_aaa_holds_the_repo_lock(tmp_path):
    """`test aaa --repo api --tasks K-1` and `test it --repo api --tasks K-1` are different real
    operations on disjoint state: B22 stages them to
    `.dop/runs/K-1/aaa-api/` and `.dop/runs/K-1/it-api/` respectively, and B23 reports them under
    the distinct projects `aaa-api` and `it-api`. Nothing about running one interferes with the
    other. Expected: they should not contend for the same lock. Observed: they do, because the
    Unit test_aaa.py and test_it.py resolve to is identical -- `test it` is refused outright
    (LockHeld) while an unrelated `test aaa` run is merely in progress on the same repo.
    """
    locks_dir = tmp_path / "locks"
    unit_aaa = Unit("repo", "K-1", "api", repo="api")
    unit_it = Unit("repo", "K-1", "api", repo="api")

    held_exc: LockHeld | None = None
    with lock(locks_dir, unit_aaa, command="dop test aaa --repo api --tasks K-1"):
        try:
            with lock(locks_dir, unit_it, command="dop test it --repo api --tasks K-1"):
                pass
        except LockHeld as exc:
            held_exc = exc

    assert held_exc is None, (
        "`dop test it --repo api --tasks K-1` was refused "
        f"({held_exc}) while `dop test aaa --repo api --tasks K-1` held the lock, even though the "
        "two commands write to different staging directories (.dop/runs/K-1/aaa-api vs "
        ".dop/runs/K-1/it-api, B22) and different report projects (aaa-api vs it-api, B23). "
        "B27's 'one writer per unit' is locking a coarser unit than the real resource: aaa and it "
        "of the same repo can never run at once, and the LockHeld message "
        f"({held_exc.holder if held_exc else None!r}) does not even say which layer holds it "
        "(Unit.label is `demand/name`, with no layer)."
    )
