"""QA front F3 — nothing outside dop's own is ever touched, and one writer per unit.

Rules: B1, B4, B10, B25, B26, B27, B34. Oracle: docs/business-rules.md.
Every test runs against the fakes on PATH (tests/fakes) or pure functions; none touches a cluster.
A test red because the product is wrong stays red.
"""

from __future__ import annotations

import io
import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from dop import lock as lockmod
from dop.config import load
from dop.lock import LockHeld, lock, lock_name
from dop.node import Node, NodeError
from dop.outcome import Status, Unit
from dop.scope import resolve
from dop.verbs import act, registry

from .conftest import FAKES, make_ctx, run_cli, worktree_dir

VERBS = {name: mod.VERB for name, mod in registry().items()}
DOP_LABELS = lambda key: {"app.kubernetes.io/managed-by": "dop", "dop/demand": key}  # noqa: E731


def _deletes(fake) -> list[list[str]]:
    return [c for c in fake.log("kubectl") if "delete" in c]


def _applies(fake) -> list[list[str]]:
    return [c for c in fake.log("kubectl") if "apply" in c]


def _results(summary_text: str) -> str:
    return summary_text


# =============================================================================================
# B26 — dop owns only what it labelled. Discovery is by label; the act is by computed name.
# =============================================================================================

def test_b26_down_does_not_delete_unlabelled_namespace_when_the_label_sits_elsewhere(root, fake):
    """A namespace named anything, carrying dop's labels for QA-1, makes QA-1 'present'. `down`
    then deletes `optum-qa-1` by name — which here is NOT labelled by dop at all. B26: down acts
    only on labelled namespaces; anything else fails the unit."""
    fake.namespaces(
        {"name": "victim", "labels": DOP_LABELS("QA-1")},
        {"name": "optum-qa-1", "labels": {}},  # somebody else's, unlabelled
    )
    code, out = run_cli(root, "down", "--tasks", "QA-1")
    deleted = [c for c in _deletes(fake) if "optum-qa-1" in c]
    assert deleted == [], f"deleted an unlabelled namespace: {deleted}\n{out}"
    assert code == 1 and "QA-1" in out


def test_b26_down_does_not_delete_a_namespace_managed_by_someone_else(root, fake):
    """Same shape, with `optum-qa-1` owned by another tool (managed-by=dop-core, like the live
    dop-ct-* namespaces)."""
    fake.namespaces(
        {"name": "anything-else", "labels": DOP_LABELS("QA-1")},
        {"name": "optum-qa-1", "labels": {"app.kubernetes.io/managed-by": "dop-core", "dop/demand": "QA-1"}},
    )
    run_cli(root, "down", "--tasks", "QA-1")
    assert [c for c in _deletes(fake) if "optum-qa-1" in c] == []


def test_b26_down_removes_node_dir_only_of_a_labelled_namespace(root, fake):
    """The node directory of an unlabelled namespace is not dop's either."""
    fake.namespaces(
        {"name": "victim", "labels": DOP_LABELS("QA-1")},
        {"name": "optum-qa-1", "labels": {}},
    )
    run_cli(root, "down", "--tasks", "QA-1")
    rm = [c for c in fake.log("docker") if "rm -rf" in " ".join(c)]
    assert rm == [], f"rm -rf ran for a namespace dop does not own: {rm}"


def test_b26_up_does_not_adopt_an_unlabelled_namespace_when_the_label_sits_elsewhere(root, fake, ws):
    """`up` refuses an unlabelled `optum-qa-1` (existing test) — unless some other namespace
    carries QA-1's labels, in which case it applies the overlay and relabels the stranger."""
    for kind_dir in ("backends",):
        (ws.paths.manifests / "demand" / kind_dir / "solo").mkdir(parents=True, exist_ok=True)
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "QA-1"), "QA-1"))
    fake.namespaces(
        {"name": "victim", "labels": DOP_LABELS("QA-1")},
        {"name": "optum-qa-1", "labels": {}},
    )
    code, out = run_cli(root, "up", "--tasks", "QA-1")
    assert _applies(fake) == [], f"applied into an unlabelled namespace:\n{out}"
    assert code == 1


def test_b26_down_claims_done_while_the_labelled_namespace_survives(root, fake):
    """Only `victim` carries QA-1's labels. `down` deletes `optum-qa-1` (absent,
    --ignore-not-found) and reports done; QA-1 stays present in every later discovery. A done
    unit that removed nothing is an outcome dop cannot claim (B10, B13)."""
    fake.namespaces({"name": "victim", "labels": DOP_LABELS("QA-1")})
    code, out = run_cli(root, "down", "--tasks", "QA-1")
    assert code != 0, f"down reported everything done:\n{out}"


def test_b26_namespace_labelled_for_another_demand_is_refused(root, fake):
    fake.namespaces({"name": "optum-qa-1", "labels": DOP_LABELS("QA-2")})
    code, out = run_cli(root, "down", "--tasks", "QA-1")
    assert code == 1 and "not labelled by dop" in out
    assert _deletes(fake) == []


def test_b26_namespace_managed_by_dop_core_is_refused(root, fake):
    fake.namespaces({"name": "optum-qa-1", "labels": {"app.kubernetes.io/managed-by": "dop-core",
                                                      "dop/demand": "QA-1"}})
    code, out = run_cli(root, "down", "--tasks", "QA-1")
    assert code == 1 and "not labelled by dop" in out
    assert _deletes(fake) == []


def test_b26_down_deletes_exactly_the_demand_namespace_and_dir(root, fake):
    fake.present("QA-1", "QA-2")
    code, _ = run_cli(root, "down", "--tasks", "QA-1")
    assert code == 0
    dels = _deletes(fake)
    assert len(dels) == 1 and dels[0][-3:] == ["namespace", "optum-qa-1", "--ignore-not-found"]
    rm = [c for c in fake.log("docker") if "rm -rf" in " ".join(c)]
    assert [c[-1] for c in rm] == ["/workspace/optum-qa-1"]
    assert all(c[1] == "k3d-test-server-0" for c in fake.log("docker"))


# ---- Node.remove guard -------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/workspace", "/workspace/", "/workspace/..", "/workspace/../etc", "/workspacex/optum-qa-1",
    "/", "workspace/optum-qa-1", "/workspace/./..",
])
def test_node_remove_refuses_outside_root(ws, fake, path):
    node = Node(ws, dry_run=False, out=io.StringIO())
    with pytest.raises(NodeError):
        node.remove(path)
    assert fake.log("docker") == []


# =============================================================================================
# B25 — every cluster call names the context; the current context is never read.
# =============================================================================================

def test_b25_every_kubectl_call_of_every_cluster_verb_names_the_context(root, fake, ws):
    (ws.paths.manifests / "demand" / "backends" / "solo").mkdir(parents=True, exist_ok=True)
    (ws.paths.manifests / "shared").mkdir(parents=True, exist_ok=True)
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "QA-1"), "QA-1"))
    fake.present("QA-1")
    for argv in (["env", "up"], ["up", "--tasks", "QA-1"], ["status"], ["log", "--no-follow"],
                 ["down", "--tasks", "QA-1"], ["report"]):
        run_cli(root, *argv)
    calls = fake.log("kubectl")
    assert calls
    bad = [c for c in calls if c[:2] != ["--context", "k3d-test"]]
    assert bad == []
    flat = [" ".join(c) for c in calls]
    assert not any("use-context" in c or "current-context" in c or "set-context" in c for c in flat)
    assert not any("--kubeconfig" in c for c in flat)


def test_b25_context_with_space_is_one_argument_and_missing_is_exit_2(root, fake):
    toml = (root / "dop.toml").read_text().replace('context = "k3d-test"', 'context = "k3d test"')
    (root / "dop.toml").write_text(toml)
    code, _ = run_cli(root, "status")
    assert code == 2
    assert fake.log("kubectl")[0][:2] == ["--context", "k3d test"]


def test_b25_context_looking_like_a_flag_is_exit_2_and_never_run(root, fake):
    toml = (root / "dop.toml").read_text().replace('context = "k3d-test"', 'context = "--kubeconfig=/dev/null"')
    (root / "dop.toml").write_text(toml)
    code, _ = run_cli(root, "down", "--tasks", "QA-1")
    assert code == 2
    assert all(c[2:4] == ["config", "get-contexts"] for c in fake.log("kubectl"))


# =============================================================================================
# B27 / B34 — one writer per unit; stale locks broken.
# =============================================================================================

def test_b27_breaking_a_stale_lock_never_deletes_a_live_one(tmp_path, monkeypatch):
    """Two processes find the same dead-pid lock. A breaks it and takes the unit; B, which read the
    dead holder before A's break, then unlinks A's *live* lock and takes the unit too — two
    writers. Interleaving forced deterministically by hooking the read."""
    u = Unit("app", "QA-1", "api")
    path = tmp_path / lock_name(u)
    dead = subprocess.Popen(["true"]); dead.wait()
    path.write_text(json.dumps({"pid": dead.pid, "host": socket.gethostname(), "command": "old"}))

    live_a = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        real_read = lockmod._read
        state = {"n": 0}

        def racing_read(p):
            got = real_read(p)
            state["n"] += 1
            if state["n"] == 1:
                # Process A, between B's read and B's unlink: breaks the stale lock and takes it.
                p.unlink()
                p.write_text(json.dumps({"pid": live_a.pid, "host": socket.gethostname(),
                                         "command": "dop deploy (A)"}))
            return got

        monkeypatch.setattr(lockmod, "_read", racing_read)
        with pytest.raises(LockHeld):
            with lock(tmp_path, u, out=io.StringIO()):
                pass  # B got in: A's lock was deleted under it
    finally:
        live_a.kill(); live_a.wait()


def _slow_kubectl(dirpath: Path) -> Path:
    """A kubectl on PATH ahead of the fake that sleeps on `logs -f` (a follow that lasts)."""
    dirpath.mkdir(parents=True, exist_ok=True)
    k = dirpath / "kubectl"
    k.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        case " $* " in *" logs "*" -f "*) sleep 6; exit 0;; esac
        exec {FAKES / 'kubectl'} "$@"
        """))
    k.chmod(0o755)
    return dirpath


def _dop(root: Path, *argv: str, env) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "from dop.cli import entry; entry()",
                             "--workspace", str(root), *argv],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def test_b27_a_reader_following_logs_does_not_lock_writers_out(root, fake, tmp_path):
    """B27 is one *writer* per unit. `dop log` follows by default and, in doing so, holds the
    (demand, app) lock for as long as the operator watches: `deploy`/`status` on that app fail
    'locked by ... (dop log ...)'."""
    fake.present("QA-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "QA-1"), "QA-1"))
    env = dict(os.environ)
    env["PATH"] = f"{_slow_kubectl(tmp_path / 'slowbin')}:{env['PATH']}"
    follower = _dop(root, "log", "--tasks", "QA-1", "--app", "solo", env=env)
    try:
        deadline = time.time() + 5
        while not list((root / ".dop" / "locks").glob("*.lock")) and time.time() < deadline:
            time.sleep(0.05)
        reader = _dop(root, "status", "--tasks", "QA-1", env=env)
        out, _ = reader.communicate(timeout=30)
        assert reader.returncode == 0, f"status blocked by a log follower:\n{out}"
    finally:
        follower.kill(); follower.wait()


def test_b27_same_unit_two_processes_second_fails_naming_holder(root, fake, tmp_path):
    """Cross-process: a unit held by a live process fails the second command, naming pid+command."""
    fake.present("QA-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "QA-1"), "QA-1"))
    env = dict(os.environ)
    env["PATH"] = f"{_slow_kubectl(tmp_path / 'slowbin')}:{env['PATH']}"
    first = _dop(root, "log", "--tasks", "QA-1", "--app", "solo", env=env)
    try:
        deadline = time.time() + 5
        while not list((root / ".dop" / "locks").glob("*.lock")) and time.time() < deadline:
            time.sleep(0.05)
        second = _dop(root, "log", "--tasks", "QA-1", "--app", "solo", "--no-follow", env=env)
        out, _ = second.communicate(timeout=30)
        assert second.returncode == 1
        assert f"pid {first.pid}" in out and "log --tasks QA-1 --app solo" in out
    finally:
        first.kill(); first.wait()
    # the killed holder's lock is left behind; the next run breaks it and says so (B34)
    code, out = run_cli(root, "log", "--tasks", "QA-1", "--app", "solo", "--no-follow")
    assert code == 0 and f"broke stale lock of dead pid {first.pid}" in out


def test_b27_lock_released_after_keyboardinterrupt_and_exception(ws):
    ctx = make_ctx(ws)
    u = Unit("app", "QA-1", "solo")
    locks = ws.state_dir / "locks"

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        act(ctx, u, interrupt)
    assert list(locks.glob("*.lock")) == []

    def boom():
        raise RuntimeError("x")

    assert act(ctx, u, boom).status is Status.FAILED
    assert list(locks.glob("*.lock")) == []


def test_b27_dry_run_takes_no_lock(ws, fake):
    fake.present("QA-1")
    ctx = make_ctx(ws, dry_run=True)
    with lock(ws.state_dir / "locks", Unit("demand", "QA-1")):
        code, out = run_cli(ws.root, "down", "--tasks", "QA-1", "--dry-run")
    assert code == 0  # planned, not refused: dry-run claims no outcome


def test_b27_unwritable_lock_dir_fails_the_unit_not_the_run(ws, fake):
    fake.present("QA-1", "QA-2")
    locks = ws.state_dir / "locks"
    locks.mkdir(parents=True)
    locks.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        code, out = run_cli(ws.root, "down", "--tasks", "QA-1", "QA-2")
    finally:
        locks.chmod(0o755)
    assert code == 1
    assert out.count("failed") >= 2 and "Permission" in out
    assert _deletes(fake) == []


@pytest.mark.parametrize("name", ["../../escape", "a/b", "..", "/abs"])
def test_b27_lock_file_never_escapes_the_lock_dir(tmp_path, name):
    d = tmp_path / "locks"
    with lock(d, Unit("suite", "QA-1", name)):
        created = [p for p in tmp_path.rglob("*.lock")]
        assert len(created) == 1 and created[0].parent == d


def test_b27_two_distinct_suites_do_not_share_a_lock(root):
    """Suite names are relative paths (config.rel_path): `e2e/x` and `e2e_x` are two valid,
    distinct suites of two apps. Their lock files collide, so one blocks the other (B13: one
    unit never stops another)."""
    toml = (root / "dop.toml").read_text()
    toml = toml.replace('suite = "fe"', 'suite = "e2e/x"')
    toml = toml.replace('[apps.solo]\n', '[apps.solo]\nsuite = "e2e_x"\n')
    (root / "dop.toml").write_text(toml)
    ws = load(root)
    assert {ws.apps["fe"].suite, ws.apps["solo"].suite} == {"e2e/x", "e2e_x"}  # config accepts both
    a = Unit("suite", "QA-1", "e2e/x")
    b = Unit("suite", "QA-1", "e2e_x")
    assert a != b
    assert lock_name(a) != lock_name(b)


def test_b34_dead_pid_on_this_host_is_broken_live_pid_is_not(tmp_path):
    u = Unit("app", "QA-1", "api")
    p = tmp_path / lock_name(u)
    # live, unrelated process on this host (pid reuse): must be treated as held, never broken
    p.write_text(json.dumps({"pid": 1, "host": socket.gethostname(), "command": "dop x"}))
    with pytest.raises(LockHeld, match="pid 1"):
        with lock(tmp_path, u):
            pass
    assert p.exists()


def test_b34_corrupt_lock_file_is_not_silently_broken(tmp_path):
    """A lock with no readable holder has no process to test: it is kept (conservative)."""
    u = Unit("app", "QA-1", "api")
    p = tmp_path / lock_name(u)
    p.write_text("")
    with pytest.raises(LockHeld):
        with lock(tmp_path, u):
            pass
    assert p.exists()


def test_b27_down_and_deploy_of_the_same_demand_do_not_exclude_each_other(ws):
    """Records the current design (units of different dimensions lock different files): a
    `deploy` holding QA-1/solo does not stop `down --tasks QA-1` deleting that namespace and its
    node dir underneath it. Weakness reported to the lead, not a B27 defect by the letter."""
    ctx = make_ctx(ws)
    with lock(ws.state_dir / "locks", Unit("app", "QA-1", "solo")):
        r = act(ctx, Unit("demand", "QA-1"), lambda: __import__("dop.outcome").outcome.done(Unit("demand", "QA-1")))
    assert r.status is Status.DONE


def test_b26_realistic_trigger_prefix_changed_down_deletes_the_wrong_namespace(root, fake):
    """No crafted labels needed: the operator changes address.namespace_prefix (or a second
    workspace with another prefix shares the cluster). QA-1 is 'present' through optum-qa-1
    (dop's own, correctly labelled); `down --tasks QA-1` then deletes `qa-qa-1` — an unlabelled
    namespace that is not dop's — and `rm -rf /workspace/qa-qa-1`, and reports done."""
    toml = (root / "dop.toml").read_text().replace('namespace_prefix = "optum"', 'namespace_prefix = "qa"')
    (root / "dop.toml").write_text(toml)
    fake.namespaces(
        {"name": "optum-qa-1", "labels": DOP_LABELS("QA-1")},
        {"name": "qa-qa-1", "labels": {"owner": "someone-else"}},
    )
    code, out = run_cli(root, "down", "--tasks", "QA-1")
    assert [c for c in _deletes(fake) if "qa-qa-1" in c] == [], out
    assert not any("/workspace/qa-qa-1" in c for c in fake.log("docker")), out


def test_b26_delete_is_guarded_by_the_labels_it_was_discovered_by(root, fake):
    """TOCTOU: discovery (get -l) and the delete (by name) are separate calls. The delete must
    itself be conditional on dop's labels (e.g. `delete namespace -l managed-by=dop,dop/demand=K`
    or a name+label re-check), else a relabel/recreate in between is deleted."""
    fake.present("QA-1")
    run_cli(root, "down", "--tasks", "QA-1")
    dels = _deletes(fake)
    assert dels
    assert all("-l" in c or "--selector" in c for c in dels), dels
