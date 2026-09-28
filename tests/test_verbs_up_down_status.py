"""up / down / status after QA (v4): B8 amended, B27 amended, B37, B38, B39, B40, D17.

Fakes only (tests/fakes); no cluster is touched and no real secret is used.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from dop.config import load
from dop.context import demand_key
from dop.lock import lock
from dop.outcome import Status, Unit, UsageError
from dop.scope import parse_worktrees, resolve
from dop.verbs import registry

from .conftest import make_ctx, run_cli, worktree_dir

VERBS = {name: mod.VERB for name, mod in registry().items()}
DOP = {"app.kubernetes.io/managed-by": "dop"}


def _seed(ws, *apps):
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        (ws.paths.manifests / "demand" / kind_dir[ws.apps[name].kind] / name).mkdir(parents=True, exist_ok=True)


def _applies(fake):
    return [c for c in fake.log("kubectl") if "apply" in c]


# -- D17: the key -------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["K-1\n", "K-١", "K-１", "K-1 ", "-1", "K-", "K1"])
def test_key_is_ascii_and_whole(raw):
    with pytest.raises(UsageError):
        demand_key(raw)


def test_key_longer_than_a_label_is_usage_error():
    with pytest.raises(UsageError, match="63"):
        demand_key("A" * 62 + "-1")
    assert demand_key("a" * 61 + "-1") == "A" * 61 + "-1"


def test_key_whose_namespace_is_too_long_is_exit_2_nothing_attempted(root, fake):
    key = "A" * 57 + "-1"  # optum-<key> is 65 characters
    code, _ = run_cli(root, "status", "--tasks", key)
    assert code == 2
    assert not [c for c in fake.log("kubectl") if "get" in c and "namespaces" in c]


# -- B8 amended: only linked worktrees -----------------------------------------------------------

def test_main_checkout_is_never_the_demands_worktree(ws, root, fake):
    (fake.state / "git" / "be.porcelain").write_text(
        f"worktree {root / 'repos/be'}\nHEAD {'a' * 40}\nbranch refs/heads/feature/K-1\n")
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    assert scope.units == []


def test_repo_dir_resolving_elsewhere_has_no_worktrees_with_a_reason(ws, root, fake):
    """git answered for another repository (its main checkout is not the repo dir)."""
    (fake.state / "git" / "solo.porcelain").write_text(
        f"worktree {root}\nHEAD {'a' * 40}\nbranch refs/heads/master\n\n"
        f"worktree {worktree_dir(root, 'ws', 'K-1')}\nHEAD {'b' * 40}\nbranch refs/heads/test/K-1\n")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    scope = resolve(make_ctx(ws, tasks=["K-1"]), VERBS["up"])
    assert [u.name for u in scope.units] == ["api"]
    (solo,) = [r for r in scope.skipped if r.unit.name == "solo"]
    assert solo.status is Status.SKIPPED and "not a git repository of its own" in solo.reason


def _git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "q", "GIT_AUTHOR_EMAIL": "q@q", "GIT_COMMITTER_NAME": "q",
                        "GIT_COMMITTER_EMAIL": "q@q", "PATH": "/usr/bin:/bin"})


def test_real_git_plain_directory_inside_a_repo(tmp_path):
    outer = tmp_path / "outer"
    _git("init", "-q", "-b", "main", str(outer))
    _git("-C", str(outer), "commit", "-q", "--allow-empty", "-m", "x")
    (outer / "plain").mkdir()
    out = subprocess.run(["git", "-C", str(outer / "plain"), "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, check=True).stdout
    assert parse_worktrees(out)[0].path.resolve() != (outer / "plain").resolve()


# -- B40: ownership at delete time ---------------------------------------------------------------

def test_down_deletes_by_selector_with_the_label_as_found(root, fake):
    fake.namespaces({"name": "optum-k-1", "labels": {**DOP, "dop/demand": "k-1"}})
    code, _ = run_cli(root, "down", "--tasks", "K-1")
    assert code == 0
    dels = [c for c in fake.log("kubectl") if "delete" in c]
    assert dels == [["--context", "k3d-test", "delete", "namespace", "-l",
                     "app.kubernetes.io/managed-by=dop,dop/demand=k-1"]]


@pytest.mark.parametrize("verb", ["up", "down", "status"])
def test_labels_on_another_namespace_fail_the_demand(root, fake, ws, verb):
    _seed(ws, "solo")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    fake.namespaces({"name": "elsewhere", "labels": {**DOP, "dop/demand": "K-1"}})
    code, out = run_cli(root, verb, "--tasks", "K-1")
    assert code == 1 and "elsewhere" in out and "optum-k-1" in out
    assert not [c for c in fake.log("kubectl") if "delete" in c or "apply" in c]
    assert not [c for c in fake.log("docker") if "rm -rf" in " ".join(c)]


# -- B27 amended: status takes no lock; down takes every app lock ------------------------------

def test_status_takes_no_lock(root, fake, ws):
    fake.present("K-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    with lock(ws.state_dir / "locks", Unit("app", "K-1", "solo")):
        code, out = run_cli(root, "status", "--tasks", "K-1")
    assert code == 0, out


def test_down_refused_while_an_app_of_the_demand_is_locked(root, fake, ws):
    fake.present("K-1")
    with lock(ws.state_dir / "locks", Unit("app", "K-1", "solo"), command="dop deploy"):
        code, out = run_cli(root, "down", "--tasks", "K-1")
    assert code == 1 and "locked" in out and "dop deploy" in out
    assert not [c for c in fake.log("kubectl") if "delete" in c]
    assert list((ws.state_dir / "locks").glob("*.lock")) == []  # every lock down took is released


# -- B37 / B38 / B39: up ---------------------------------------------------------------------------

@pytest.fixture
def k1(root, fake, ws):
    _seed(ws, "api", "be", "fe")
    fake.present()
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    return root


def test_up_app_renders_the_whole_demand_and_applies_only_the_app(k1, fake, ws):
    code, out = run_cli(k1, "up", "--tasks", "K-1", "--app", "be")
    assert code == 0, out
    k = json.loads((ws.state_dir / "overlays" / "K-1" / "kustomization.yaml").read_text())
    assert {"backends/api", "backends/be", "frontends/fe"} <= set(k["resources"])
    cms = {json.loads(p["patch"])["metadata"]["name"]: json.loads(p["patch"])["data"]
           for p in k["patches"] if p["target"]["kind"] == "ConfigMap"}
    assert cms["optum-urls"] == {"API_URL": "http://api:8082"}
    assert cms["optum-env"] == {"SCHEDULING_ENABLED": "false"}
    (apply,) = _applies(fake)
    assert apply[-2:] == ["-l", "dop/app notin (api,fe)"]
    assert "K-1/api" not in out and "K-1/be" in out


def test_up_without_app_applies_everything(k1, fake):
    run_cli(k1, "up", "--tasks", "K-1")
    (apply,) = _applies(fake)
    assert "-l" not in apply


def test_up_secret_file_is_gone_after_apply_and_after_a_failed_apply(k1, fake, ws):
    secret = ws.state_dir / "overlays" / "K-1" / "secret.env"
    assert run_cli(k1, "up", "--tasks", "K-1")[0] == 0
    assert not secret.exists()
    fake.kube_fail()  # every call after the context check fails, the apply included
    code, _ = run_cli(k1, "up", "--tasks", "K-1")
    assert code == 1
    assert not secret.exists()


def test_up_dry_run_writes_nothing_and_still_fails_what_would_fail(k1, fake, ws, root):
    code, out = run_cli(k1, "up", "--tasks", "K-1", "--app", "be", "--dry-run")
    assert code == 0 and "[dry-run]" in out and "dop/app notin (api,fe)" in out
    assert not ws.state_dir.exists()
    (root / "docker" / ".env").write_text("OTHER=1\n")
    code, out = run_cli(k1, "up", "--tasks", "K-1", "--dry-run")
    assert code == 1 and "DB_PASSWORD" in out
    assert not ws.state_dir.exists()


def test_up_refuses_a_cluster_scoped_kind_in_the_base(k1, fake, tmp_path, monkeypatch):
    """The rendered demand is checked before the apply; `kubectl kustomize` is faked here to print
    a ClusterRole beside the namespace."""
    bindir = tmp_path / "kbin"
    bindir.mkdir()
    k = bindir / "kubectl"
    from .conftest import FAKES
    k.write_text("#!/bin/sh\n"
                 'case " $* " in *" kustomize "*) printf "kind: Namespace\\n---\\nkind: ClusterRole\\n"; exit 0;; esac\n'
                 f'exec {FAKES / "kubectl"} "$@"\n')
    k.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{__import__('os').environ['PATH']}")
    code, out = run_cli(k1, "up", "--tasks", "K-1")
    assert code == 1 and "ClusterRole" in out
    assert not _applies(fake)
