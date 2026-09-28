"""dop env up / up / down / status / log (§5, B4, B10, B18–B20, B26, B30).

Most scenarios go through the CLI with the shared subprocess fakes (kubectl/docker), the same way
tests/test_cli.py and tests/test_scope.py do. The fake kubectl answers `get deployment ... -o json`
with nothing for any name it was not told about, which is exactly the "never deployed yet" shape
(B19's "waiting for deploy" note); the one scenario that needs a *ready* Deployment back is tested
by handing the verb's own `_run_one`/`_ready` a small in-process fake `Kube` instead of extending
the shared fixture, which two other builders are using at the same time.
"""

from __future__ import annotations

import json

import pytest

from dop.outcome import Status, Unit
from dop.verbs import status, up

from .conftest import make_ctx, run_cli, worktree_dir


def _kubectl(fake):
    return fake.log("kubectl")


def _docker(fake):
    return fake.log("docker")


def _applied(calls, overlay=None) -> bool:
    """Every logged call carries `--context <ctx>` first (B25); match on the tail."""
    for c in calls:
        if len(c) >= 4 and c[-3] == "apply" and c[-2] == "-k" and (overlay is None or c[-1] == str(overlay)):
            return True
    return False


def _deleted(calls, key) -> bool:
    """B40: `down` deletes by dop's labels for the demand, never by the computed name."""
    selector = f"app.kubernetes.io/managed-by=dop,dop/demand={key}"
    return any(c[-4:] == ["delete", "namespace", "-l", selector] for c in calls)


def _seed_manifests(ws, *apps: str) -> None:
    """render_demand only needs each app's base directory to exist (it never reads inside it —
    the actual patch targets are workspace-3's job); tests/fakes/kubectl ignores `apply -k` content
    too, so an empty directory per app is enough to exercise `up`/`down` end to end."""
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        app = ws.apps[name]
        (ws.paths.manifests / "demand" / kind_dir[app.kind] / name).mkdir(parents=True, exist_ok=True)


# -- env up (B4 shared namespace, B23 reports dir seeded) ---------------------------------------

def test_env_up_applies_shared_manifests_and_seeds_reports_dir(ws, fake):
    (ws.paths.manifests / "shared").mkdir(parents=True)
    code, out = run_cli(ws.root, "env", "up")
    assert code == 0
    assert _applied(_kubectl(fake), ws.paths.manifests / "shared")
    docker_calls = _docker(fake)
    assert [c[0] for c in docker_calls] == ["exec", "cp", "exec", "exec", "exec"]
    assert docker_calls[3][-1] == f"/workspace/{ws.address.shared_namespace}/reports"  # sync script's destination arg


def test_env_up_is_idempotent(ws, fake):
    (ws.paths.manifests / "shared").mkdir(parents=True)
    run_cli(ws.root, "env", "up")
    code, _ = run_cli(ws.root, "env", "up")
    assert code == 0


def test_env_up_dry_run_changes_nothing(ws, fake):
    (ws.paths.manifests / "shared").mkdir(parents=True)
    code, out = run_cli(ws.root, "env", "up", "--dry-run")
    assert code == 0
    assert not _applied(_kubectl(fake))
    assert _docker(fake) == []
    assert "[dry-run]" in out
    assert not (ws.state_dir / "seed").exists()  # B38: not even the host-side placeholder


def test_env_up_seeds_never_syncs_the_reports_dir(ws, fake):
    """D7: the seed may only add a missing index.html; the replacing sync would wipe published
    reports."""
    from dop.node import _SEED_SCRIPT, _SYNC_SCRIPT

    (ws.paths.manifests / "shared").mkdir(parents=True)
    run_cli(ws.root, "env", "up")
    scripts = [c[4] for c in _docker(fake) if c[:1] == ["exec"]]
    assert _SEED_SCRIPT in scripts and _SYNC_SCRIPT not in scripts


# -- up (B10 enters, B18 wiring, B19 companions/zero-apps, B20/B32 scheduler out of my scope) ----

def test_up_renders_overlay_and_applies_it(ws, root, fake):
    fake.present()  # nothing present yet: up must still enter it (B10, B12 entering)
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    _seed_manifests(ws, "solo")
    code, out = run_cli(root, "up", "--tasks", "K-1")
    assert code == 0  # done-with-note still counts as done, not partial

    overlay = ws.state_dir / "overlays" / "K-1"
    ns_doc = json.loads((overlay / "namespace.yaml").read_text())
    assert ns_doc["metadata"]["name"] == "optum-k-1"
    assert ns_doc["metadata"]["labels"] == {
        "app.kubernetes.io/managed-by": "dop", "dop/demand": "K-1",
    }
    assert _applied(_kubectl(fake), overlay)
    assert "waiting for deploy" in out  # B19: nothing copied into the node yet


def test_up_zero_apps_is_skipped_without_a_namespace(ws, root, fake):
    fake.present()
    code, out = run_cli(root, "up", "--tasks", "K-9")
    assert code == 3
    assert "no application of the demand has a worktree" in out
    assert not _applied(_kubectl(fake))
    assert not (ws.state_dir / "overlays" / "K-9").exists()


def test_up_dry_run_does_not_touch_the_cluster(ws, root, fake):
    fake.present()
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    _seed_manifests(ws, "solo")
    code, out = run_cli(root, "up", "--tasks", "K-1", "--dry-run")
    assert code == 0  # B15: a dry-run's planned units count as done (outcome.py exit_code)
    assert "planned  K-1/solo" in out
    assert not _applied(_kubectl(fake))


def test_up_ready_deployment_is_done_with_no_note(ws):
    """The fake kubectl only ever answers "not found"; the ready branch is exercised directly
    against `_ready`, with a minimal stand-in for Kube (not the shared subprocess fixture)."""

    class ReadyKube:
        def json(self, args):
            assert args[:3] == ["get", "deployment", "be"]
            return {"spec": {"replicas": 2}, "status": {"readyReplicas": 2}}

    ctx = make_ctx(ws, tasks=["K-1"])
    ctx.kube = ReadyKube()  # cached_property: plain assignment overrides it
    unit = Unit("app", "K-1", "be")
    result = up._run_one(ctx, unit)
    assert result.status is Status.DONE
    assert result.reason == ""


def test_up_not_ready_deployment_notes_waiting_for_deploy(ws):
    class NotReadyKube:
        def json(self, args):
            return {"spec": {"replicas": 2}, "status": {"readyReplicas": 1}}

    ctx = make_ctx(ws, tasks=["K-1"])
    ctx.kube = NotReadyKube()
    result = up._run_one(ctx, Unit("app", "K-1", "be"))
    assert result.status is Status.DONE
    assert result.reason == "waiting for deploy"


# -- down (B10 leaves, B26 only what dop owns, node dir removed) --------------------------------

def test_down_deletes_namespace_and_node_dir(ws, fake):
    fake.present("K-1")
    code, out = run_cli(ws.root, "down", "--tasks", "K-1")
    assert code == 0
    assert _deleted(_kubectl(fake), "K-1")
    rm_calls = [c for c in _docker(fake) if c[:1] == ["exec"] and "rm -rf" in " ".join(c)]
    assert any(c[-1] == "/workspace/optum-k-1" for c in rm_calls)


def test_down_is_idempotent(ws, fake):
    fake.present("K-1")
    run_cli(ws.root, "down", "--tasks", "K-1")
    fake.present()  # the namespace is gone now
    code, _ = run_cli(ws.root, "down", "--tasks", "K-1")
    assert code == 3  # B26/resolve: not present any more, nothing to do


def test_down_dry_run_changes_nothing(ws, fake):
    fake.present("K-1")
    code, out = run_cli(ws.root, "down", "--tasks", "K-1", "--dry-run")
    assert code == 0
    assert not _deleted(_kubectl(fake), "K-1")
    assert _docker(fake) == []


# -- status (readiness, address, B32 scheduler note, B19 trunk source) --------------------------

def test_status_reports_not_deployed_and_address(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "status")
    assert code == 0
    assert "K-1/solo: not deployed · http://solo.k-1.localhost:8080 · scheduler: on (not controllable)" in out


def test_status_marks_the_companion_as_trunk(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    # dry-run: wiring is printed as expected, not checked against a live ConfigMap the fake lacks (B18)
    code, out = run_cli(root, "--dry-run", "status")
    assert code == 0
    line = next(l for l in out.splitlines() if l.startswith("K-1/fe:"))
    assert "source: trunk (main@" in line


def test_status_of_an_unreadable_cluster_raises_for_act_to_fail_not_hide(ws):
    """`_run_one` does not swallow a read failure into some "down" reading: it raises, and `act`
    (tested in test_tools.py::test_act_turns_exceptions_into_failed) turns that into failed."""
    from dop.kube import KubeError

    class BrokenKube:
        def json(self, args):
            raise KubeError("refused")

    ctx = make_ctx(ws, tasks=["K-1"], apps=["solo"])
    ctx.kube = BrokenKube()
    with pytest.raises(KubeError):
        status._run_one(ctx, Unit("app", "K-1", "solo"), {"solo"})


# -- log (follow only a single unit; several units are listed, not guessed; --no-follow dumps) --

def test_log_follows_the_single_matched_unit(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "log", "--tasks", "K-1")
    assert code == 0
    calls = [c for c in _kubectl(fake) if c[2:4] == ["logs", "-n"]]
    (call,) = calls
    assert call[4] == "optum-k-1" and call[5] == "deployment/solo" and call[-1] == "-f"
    assert "==> K-1/solo <==" in out


def test_log_no_follow_dumps_without_the_flag(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "log", "--tasks", "K-1", "--no-follow")
    assert code == 0
    (call,) = [c for c in _kubectl(fake) if c[2:4] == ["logs", "-n"]]
    assert call[-1] != "-f"


def test_log_several_units_are_listed_not_guessed(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    code, out = run_cli(root, "log", "--tasks", "K-1")
    assert code == 3  # B14: something skipped
    assert "skipped  K-1/be" in out
    assert "skipped  K-1/fe" in out
    assert not any(c[2:4] == ["logs", "-n"] for c in _kubectl(fake))


def test_log_several_units_no_follow_dumps_every_one(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    code, out = run_cli(root, "log", "--tasks", "K-1", "--no-follow")
    assert code == 0
    calls = [c for c in _kubectl(fake) if c[2:4] == ["logs", "-n"]]
    assert len(calls) == 2
    assert "==> K-1/be <==" in out and "==> K-1/fe <==" in out
