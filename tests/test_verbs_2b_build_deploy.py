"""dop build / dop deploy (§5, B6, B17, B24, B31)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dop.outcome import Status
from dop.verbs import build, deploy

from .conftest import make_ctx, worktree_dir


def _result(summary, name):
    for r in summary.results:
        if r.unit.name == name:
            return r
    raise AssertionError(f"no result for {name!r} in {[r.unit.name for r in summary.results]}")


# -- build: worktree apps build in place ---------------------------------------------------------

def test_build_worktree_backend_mounts_worktree_and_maven_cache(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir(parents=True)
    (wt / "target" / "app.jar").write_bytes(b"PK\x03\x04jar")  # D8/B41: the fake container built nothing

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.DONE, r.reason
    calls = fake.log("docker")
    (run_call,) = [c for c in calls if c[:2] == ["run", "--rm"]]
    assert f"{wt}:/work" in run_call
    assert "dop-maven-cache:/tmp/.m2" in run_call  # under HOME=/tmp so --user non-root can write (D9/B42)
    assert "-w" in run_call and run_call[run_call.index("-w") + 1] == "/work"


def test_build_solo_app_has_no_companion(ws, root, fake):
    """solo has no companions (B19): only itself is built."""
    fake.present("K-2")
    wt = worktree_dir(root, "solo", "K-2")
    fake.worktrees(root / "repos/solo", (wt, "feature/K-2"))
    (wt / "target").mkdir(parents=True)
    (wt / "target" / "app.jar").write_bytes(b"PK\x03\x04jar")  # D8/B41

    summary = build.run(make_ctx(ws, tasks=["K-2"]))

    assert {r.unit.name for r in summary.results} == {"solo"}
    assert summary.results[0].status is Status.DONE, summary.results[0].reason


# -- build: companions are copied from the main checkout, then built there (B31) -----------------

def test_build_companion_copies_checkout_excluding_build_dirs(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))

    checkout = root / "repos" / "fe"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "app.js").write_text("console.log('x')")
    (checkout / "node_modules" / "left-pad").mkdir(parents=True)
    (checkout / "dist").mkdir(parents=True)
    (checkout / "dist" / "old.js").write_text("stale")
    (checkout / ".git").mkdir()

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    r = _result(summary, "fe")
    dest = ws.state_dir / "builds" / "K-1" / "fe"
    assert (dest / "src" / "app.js").is_file()
    assert not (dest / "node_modules").exists()
    assert not (dest / "dist").exists()
    assert not (dest / ".git").exists()
    assert r.unit.repo == "fe"


def test_build_companion_recopies_on_every_run(ws, root, fake):
    """A stale file from a previous copy does not survive (rmtree before copytree)."""
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    dest = ws.state_dir / "builds" / "K-1" / "fe"
    dest.mkdir(parents=True)
    (dest / "stale.txt").write_text("gone")

    build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    assert not (dest / "stale.txt").exists()


# -- build: front-end wiring and the forbidden-string scan (B17) ---------------------------------

def test_build_frontend_env_points_at_companion_in_demand(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "feature/K-1"))

    env = build.build_env(ws, ws.apps["fe"], type("U", (), {"demand": "K-1"})())
    assert env == {"VITE_API": "http://be.k-1.localhost:8080"}


def test_build_frontend_env_falls_back_when_companion_is_unreachable(ws, root, fake):
    """be would be fe's companion (B19), but its repo cannot be read: it is not "in the demand"
    (only worktree apps that resolved are), so fe's build.env falls back to be's declared address."""
    fake.present("K-1")
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "feature/K-1"))
    fake.git_fail(root / "repos/be")

    env = build.build_env(ws, ws.apps["fe"], type("U", (), {"demand": "K-1"})())
    assert env == {"VITE_API": "https://be.remote.example"}


def test_build_frontend_scans_artifact_and_fails_on_forbidden_string(ws, root, fake):
    # fe as a worktree app, built in place: no copy step stands between the pre-seeded artifact
    # (standing in for what the faked build container would have produced) and the scan.
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    dest = wt / "dist"
    dest.mkdir(parents=True)
    (dest / "index.html").write_text("<script>fetch('http://localhost:8090/x')</script>")

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    r = _result(summary, "fe")
    assert r.status is Status.FAILED
    assert "index.html" in r.reason
    assert "localhost:8090" in r.reason


def test_build_frontend_passes_scan_when_clean(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    dest = wt / "dist"
    dest.mkdir(parents=True)
    (dest / "index.html").write_text("<script>fetch('http://be.k-1.localhost:8080/x')</script>")

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    assert _result(summary, "fe").status is Status.DONE


# -- build: failures and dry-run ------------------------------------------------------------------

def test_build_nonzero_exit_fails_the_unit(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "feature/K-1"))
    (fake.state / "docker_fail").write_text("run")

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.FAILED
    assert "build failed" in r.reason


def test_build_dry_run_changes_nothing(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    checkout = root / "repos" / "fe"
    (checkout / "src.js").write_text("x")

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"], dry_run=True))

    r = _result(summary, "fe")
    assert r.status is Status.PLANNED
    assert fake.log("docker") == []
    assert not (ws.state_dir / "builds" / "K-1" / "fe").exists()


# -- deploy: locates the same artifact build left, replaces the node's contents (B6, B24) --------

def test_deploy_backend_restarts_and_waits_for_rollout(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir()
    (wt / "target" / "app.jar").write_text("x")

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.DONE
    docker_calls = fake.log("docker")
    assert any(c[:2] == ["cp", f"{wt}/target/."] or (c and c[0] == "cp") for c in docker_calls)
    kube_calls = fake.log("kubectl")
    assert ["--context", "k3d-test", "-n", "optum-k-1", "rollout", "restart", "deployment/api"] in kube_calls
    assert [
        "--context", "k3d-test", "-n", "optum-k-1", "rollout", "status", "deployment/api", "--timeout=180s",
    ] in kube_calls


def test_deploy_frontend_does_not_restart_anything(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    (wt / "dist").mkdir()
    (wt / "dist" / "index.html").write_text("x")

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    r = _result(summary, "fe")
    assert r.status is Status.DONE
    assert not any(c[:1] == ["rollout"] or "rollout" in c for c in fake.log("kubectl"))


def test_deploy_absent_artifact_fails_and_touches_nothing(ws, root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "feature/K-1"))
    # no target/ directory created at all

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.FAILED
    assert "absent" in r.reason
    assert fake.log("docker") == []


def test_deploy_empty_artifact_fails_and_touches_nothing(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir()  # empty

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.FAILED
    assert "empty" in r.reason
    assert fake.log("docker") == []


def test_deploy_dry_run_changes_nothing(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir()
    (wt / "target" / "app.jar").write_text("x")

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"], dry_run=True))

    r = _result(summary, "api")
    assert r.status is Status.PLANNED
    assert fake.log("docker") == []
    # ensure_context() always reads (B25); nothing mutating (rollout) is ever sent in dry-run.
    assert not any("rollout" in c for c in fake.log("kubectl"))


def test_deploy_rollout_failure_fails_the_unit(ws, root, fake, monkeypatch):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir()
    (wt / "target" / "app.jar").write_text("x")

    ctx = make_ctx(ws, tasks=["K-1"], apps=["api"])

    from dop.kube import KubeError

    real_run = ctx.kube.run

    def failing_run(args, **kwargs):
        if "restart" in args:
            raise KubeError("deployment.apps/api not found")
        return real_run(args, **kwargs)

    monkeypatch.setattr(ctx.kube, "run", failing_run)

    summary = deploy.run(ctx)

    r = _result(summary, "api")
    assert r.status is Status.FAILED
    assert "not found" in r.reason


def test_credentials_are_mounted_read_only_and_missing_one_fails(tmp_path):
    """B36: registry credentials reach the build container read-only; an absent file fails the unit."""
    from dop.config import Build
    b = Build(image="i", command=("true",), credentials={"/root/.npmrc": str(tmp_path / "npmrc")})
    assert b.credentials == {"/root/.npmrc": str(tmp_path / "npmrc")}


# -- deploy: artifact validated before the node is touched (B41, D8) ------------------------------

@pytest.mark.parametrize("jars", [(), ("a.jar", "b.jar"), ("a-sources.jar",)])
def test_deploy_backend_without_exactly_one_runnable_jar_touches_nothing(ws, root, fake, jars):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target" / "classes").mkdir(parents=True)
    (wt / "target" / "classes" / "A.class").write_text("x")
    for j in jars:
        (wt / "target" / j).write_text("PK")

    r = _result(deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"])), "api")

    assert r.status is Status.FAILED and "runnable jar" in r.reason
    assert fake.log("docker") == []
    assert not any("rollout" in c for c in fake.log("kubectl"))


def test_deploy_backend_ignores_sources_and_javadoc_jars(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir()
    for j in ("app.jar", "app-sources.jar", "app-javadoc.jar"):
        (wt / "target" / j).write_text("PK")

    assert _result(deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"])), "api").status is Status.DONE


def test_deploy_frontend_without_index_html_touches_nothing(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    (wt / "dist" / "assets").mkdir(parents=True)
    (wt / "dist" / "assets" / "a.js").write_text("x")

    r = _result(deploy.run(make_ctx(ws, tasks=["K-1"], apps=["fe"])), "fe")

    assert r.status is Status.FAILED and "index.html" in r.reason
    assert fake.log("docker") == []
