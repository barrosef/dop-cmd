"""B7, B11, B13, B14, B15, B25 through the command line."""

import pytest

from dop.outcome import RunSummary, Unit, done, failed, planned, skipped
from dop.verbs import VERB_MODULES, registry

from .conftest import run_cli, worktree_dir


def test_registry_has_every_verb():
    assert list(registry()) == [
        "env up", "up", "build", "deploy", "down", "status", "log",
        "test aaa", "test it", "test e2e", "report",
    ]
    assert len(VERB_MODULES) == 11


# -- B14 exit contract --------------------------------------------------------------------------

def test_exit_codes():
    u = Unit("app", "K-1", "a")
    assert RunSummary([done(u)]).exit_code() == 0
    assert RunSummary([done(u), skipped(u, "r")]).exit_code() == 3
    assert RunSummary([]).exit_code() == 3
    assert RunSummary([done(u), skipped(u, "r"), failed(u, "r")]).exit_code() == 1
    assert RunSummary([planned(u)]).exit_code() == 0


def test_summary_lists_every_non_done_unit():
    import io
    out = io.StringIO()
    RunSummary([done(Unit("app", "K-1", "a")), skipped(Unit("app", "K-1", "b"), "why"),
                failed(Unit("app", "K-2", "c"), "boom")]).render(out)
    text = out.getvalue()
    assert "K-1/b: why" in text and "K-2/c: boom" in text and "K-1/a" not in text


@pytest.mark.parametrize("argv", [
    [],                       # no verb
    ["test"],                 # verb group without verb
    ["frobnicate"],           # unknown verb
    ["up"],                   # up without --tasks
    ["down"],                 # down without --tasks
    ["up", "--tasks", "1530"],          # B7: not a key
    ["up", "--tasks", "SUOPT_1530"],
    ["status", "--repo", "api"],        # status takes no --repo
    ["status", "--app", "ghost"],       # unknown app
])
def test_usage_errors_exit_2(root, fake, argv):
    code, _ = run_cli(root, *argv)
    assert code == 2


def test_key_is_upper_cased(root, fake):
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "SUOPT-1530"), "feature/suopt-1530"))
    code, out = run_cli(root, "up", "--tasks", "suopt-1530")
    assert "SUOPT-1530/solo" in out
    assert code == 3  # stub: every unit skipped


# -- B25 context --------------------------------------------------------------------------------

def test_missing_context_exits_2(root, fake):
    fake.contexts("client-prod")
    code, _ = run_cli(root, "status")
    assert code == 2
    # nothing but the context check ran
    assert all(call[:2] == ["--context", "k3d-test"] and call[2:4] == ["config", "get-contexts"]
               for call in fake.log("kubectl"))


def test_every_kubectl_call_names_the_context(root, fake):
    fake.present("K-1")
    fake.namespaces(
        {"name": "optum-k-1", "labels": {"app.kubernetes.io/managed-by": "dop", "dop/demand": "K-1"}},
        {"name": "optum-k-2", "labels": {}},
    )
    run_cli(root, "down", "--tasks", "K-1", "K-2", "K-3")
    calls = fake.log("kubectl")
    assert calls
    assert all(c[:2] == ["--context", "k3d-test"] for c in calls)


# -- B11 / B13 / B14 through the CLI ------------------------------------------------------------

def test_nothing_present_exits_3_and_says_so(root, fake):
    fake.present()
    code, out = run_cli(root, "status")
    assert code == 3
    assert "nothing present" in out


def test_one_failed_unit_does_not_stop_others_and_exits_1(root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"), (root, "K-1-copy"))
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "status")
    assert code == 1
    assert "failed   K-1/api" in out
    assert "skipped  K-1/solo" in out


def test_cluster_unreadable_exits_1(root, fake):
    fake.kube_fail()
    code, out = run_cli(root, "status")
    assert code == 1 and "cannot read the cluster" in out


# -- B15 dry-run --------------------------------------------------------------------------------

@pytest.mark.parametrize("where", ["before", "after"])
def test_dry_run_flag_either_side(root, fake, where):
    fake.present()
    argv = ["--dry-run", "status"] if where == "before" else ["status", "--dry-run"]
    code, out = run_cli(root, *argv)
    assert "dry-run: nothing was changed" in out
