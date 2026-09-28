"""B8, B9, B11, B12, B19, B26, B28, B29: from filters to units."""

import pytest

from dop.outcome import Status, UsageError
from dop.scope import branch_matches, parse_worktrees, resolve
from dop.verbs import registry

from .conftest import make_ctx, worktree_dir

VERBS = {name: mod.VERB for name, mod in registry().items()}


def names(scope):
    return sorted((u.demand, u.name) for u in scope.units)


def results(scope):
    return {(r.unit.demand, r.unit.name): (r.status, r.reason) for r in scope.skipped}


# -- B28 token --------------------------------------------------------------------------------

@pytest.mark.parametrize("branch, hit", [
    ("SUOPT-1530", True),
    ("feature/suopt-1530", True),
    ("feature/SUOPT-1530-pre-fat", True),
    ("fix_suopt-1530.v2", True),
    ("suopt-15300", False),
    ("xsuopt-1530", False),
    ("feature/suopt-153", False),
    ("suopt-1530x", False),
    (None, False),
])
def test_branch_token(branch, hit):
    assert branch_matches("SUOPT-1530", branch) is hit


def test_parse_real_porcelain_shape():
    text = (
        "worktree /r\nHEAD aaa\nbranch refs/heads/main\n\n"
        "worktree /r/.wt/x\nHEAD bbb\ndetached\n\n"
        "worktree /r/.wt/y\nHEAD ccc\nbranch refs/heads/feature/K-1\nprunable gitdir file points to non-existent location\n"
    )
    wts = parse_worktrees(text)
    assert [w.branch for w in wts] == ["main", None, "feature/K-1"]
    assert wts[0].main and not wts[1].main
    assert wts[2].prunable


# -- B8 / B9 / B19 ------------------------------------------------------------------------------

def test_up_worktree_apps_plus_companions(ws, root, fake):
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    by_name = {u.name: u for u in scope.units}
    assert set(by_name) == {"be", "fe"}
    assert by_name["be"].source == "worktree"
    assert by_name["be"].path == worktree_dir(root, "be", "K-1")
    assert by_name["fe"].source == "trunk"  # companion from the main checkout (B19)
    assert by_name["fe"].path == root / "repos/fe"
    assert by_name["fe"].ref == "main@aaaaaaa"  # B31: actual branch and sha


def test_companion_ref_marks_dirty(ws, root, fake):
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    (fake.state / "git" / "fe.status").write_text(" M src/x.ts\n")
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    fe = next(u for u in scope.units if u.name == "fe")
    assert fe.ref == "main@aaaaaaa dirty"


def test_worktree_app_is_not_duplicated_as_companion(ws, root, fake):
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    assert {(u.name, u.source) for u in scope.units} == {("be", "worktree"), ("fe", "worktree")}


def test_demand_with_no_app_is_skipped(ws, root, fake):
    scope = resolve(make_ctx(ws, tasks=["K-9"]), VERBS["up"])
    assert scope.units == []
    assert results(scope)[("K-9", None)][0] is Status.SKIPPED


def test_ambiguous_worktree_fails_that_repo_only(ws, root, fake):
    fake.worktrees(root / "repos/api",
                   (worktree_dir(root, "api", "K-1"), "K-1"),
                   (root / "elsewhere", "feature/k-1-bis"))
    (root / "elsewhere").mkdir()
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    assert names(scope) == [("K-1", "solo")]
    status, reason = results(scope)[("K-1", "api")]
    assert status is Status.FAILED
    assert "more than one" in reason and "feature/k-1-bis" in reason and "K-1" in reason


def test_missing_worktree_path_fails_the_unit(ws, root, fake):
    fake.worktrees(root / "repos/solo", (root / "gone", "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    status, reason = results(scope)[("K-1", "solo")]
    assert status is Status.FAILED and str(root / "gone") in reason


def test_git_failure_fails_that_repo_units(ws, root, fake):
    fake.git_fail(root / "repos/api")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    assert names(scope) == [("K-1", "solo")]
    assert results(scope)[("K-1", "api")][0] is Status.FAILED


def test_app_filter_narrows_never_widens(ws, root, fake):
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"], apps=["be", "solo"]), VERBS["up"])
    assert names(scope) == [("K-1", "be")]


def test_unknown_app_filter_is_usage_error(ws, fake):
    with pytest.raises(UsageError):
        resolve(make_ctx(ws, tasks=["K-1"], apps=["ghost"]), VERBS["up"])


# -- B11 / B12 / B26 ----------------------------------------------------------------------------

def test_no_filter_means_every_present_demand(ws, root, fake):
    fake.present("K-1", "K-2")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"),
                   (worktree_dir(root, "solo", "K-2"), "K-2"))
    scope = resolve(make_ctx(ws), VERBS["status"])
    assert names(scope) == [("K-1", "solo"), ("K-2", "solo")]


def test_only_labelled_namespaces_are_present(ws, root, fake):
    fake.namespaces(
        {"name": "optum-k-1", "labels": {"app.kubernetes.io/managed-by": "dop", "dop/demand": "K-1"}},
        {"name": "optum-k-2", "labels": {"dop/demand": "K-2"}},  # not managed-by dop
        {"name": "kube-system", "labels": {}},
    )
    scope = resolve(make_ctx(ws), VERBS["report"])
    assert names(scope) == [("K-1", None)]


def test_nothing_present_says_so(ws, fake):
    fake.present()
    scope = resolve(make_ctx(ws), VERBS["status"])
    assert scope.units == [] and scope.skipped == []
    assert "nothing present" in scope.note


def test_tasks_filter_narrows_present(ws, root, fake):
    fake.present("K-1", "K-2")
    scope = resolve(make_ctx(ws, tasks=["K-2", "K-3"]), VERBS["report"])
    assert names(scope) == [("K-2", None)]
    assert results(scope)[("K-3", None)] == (Status.SKIPPED, "not present in the environment")


def test_down_requires_tasks(ws, fake):
    with pytest.raises(UsageError):
        resolve(make_ctx(ws), VERBS["down"])


def test_down_refuses_unlabelled_namespace(ws, fake):
    fake.namespaces({"name": "optum-k-7", "labels": {}})
    scope = resolve(make_ctx(ws, tasks=["K-7"]), VERBS["down"])
    assert scope.units == []
    status, reason = results(scope)[("K-7", None)]
    assert status is Status.FAILED and "not labelled by dop" in reason


def test_up_refuses_unlabelled_namespace(ws, root, fake):
    fake.namespaces({"name": "optum-k-7", "labels": {}})
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-7"), "K-7"))
    scope = resolve(make_ctx(ws, tasks=["K-7"]), VERBS["up"])
    assert scope.units == []
    assert results(scope)[("K-7", None)][0] is Status.FAILED


def test_cluster_unreadable_fails_units(ws, fake):
    fake.kube_fail()
    scope = resolve(make_ctx(ws), VERBS["status"])
    assert scope.units == []
    assert [r.status for r in scope.skipped] == [Status.FAILED]
    assert "cannot read the cluster" in scope.skipped[0].reason


# -- units per dimension, B29 -----------------------------------------------------------------

def test_suite_units_follow_apps(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws), VERBS["test e2e"])
    assert [(u.demand, u.name, u.app) for u in scope.units] == [("K-1", "fe", "fe")]


def test_repo_units_and_repo_filter(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws), VERBS["test aaa"])
    assert names(scope) == [("K-1", "api"), ("K-1", "be")]  # companions are not tested
    scope = resolve(make_ctx(ws, repos=["be", "fe"]), VERBS["test aaa"])
    assert names(scope) == [("K-1", "be")]


def test_unknown_repo_filter_is_usage_error(ws, fake):
    with pytest.raises(UsageError):
        resolve(make_ctx(ws, repos=["ghost"]), VERBS["test aaa"])


def test_env_up_is_one_shared_unit(ws, fake):
    scope = resolve(make_ctx(ws), VERBS["env up"])
    assert [(u.kind, u.name) for u in scope.units] == [("shared", "optum-shared")]
