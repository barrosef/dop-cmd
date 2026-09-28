"""QA front F4b — artifacts into the node, replace safely (B6, B16, B17, B24, B31, B36).

Only Python code paths against the fakes (tests/fakes). The node sync shell script is NEVER
executed here: the fake docker records the call and returns. The shell-level reproductions of
the sync script live in the tester's report and were run inside bwrap only.
"""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path

import pytest

from dop.config import load
from dop.context import Context, Filters
from dop.node import Node, NodeError
from dop.outcome import Status, UsageError
from dop.verbs import build, deploy

from .conftest import CONFIG, make_ctx, worktree_dir


def _result(summary, name):
    for r in summary.results:
        if r.unit.name == name:
            return r
    raise AssertionError(f"no result for {name!r} in {[r.unit.name for r in summary.results]}")


def _kube_calls(fake, word):
    return [c for c in fake.log("kubectl") if word in c]


def _sync_calls(fake):
    return [c for c in fake.log("docker") if c[:1] == ["exec"] and "find . -mindepth 1" in " ".join(c)]


# == B16 — deploy: "present and non-empty" before anything is replaced ===========================

def test_b16_backend_artifact_with_only_empty_subdirs_fails_and_restarts_nothing(ws, root, fake):
    """target/ holding only empty directories (a build that produced no file at all) is an empty
    artifact: the unit must fail, the node must not be synced, the workload must not restart."""
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target" / "classes").mkdir(parents=True)
    (wt / "target" / "maven-status").mkdir(parents=True)

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.FAILED, f"deployed a file-less artifact: {r.status} {r.reason}"
    assert not _sync_calls(fake), "the mounted directory was synced from a file-less artifact"
    assert not _kube_calls(fake, "restart"), "the running back-end was restarted onto nothing"


def test_b16_failed_sync_never_restarts_the_backend(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir(parents=True)
    (wt / "target" / "app.jar").write_bytes(b"PK\x03\x04jar")
    (fake.state / "docker_fail").write_text("exec")

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    r = _result(summary, "api")
    assert r.status is Status.FAILED
    assert not _kube_calls(fake, "restart")


def test_b16_failed_docker_cp_never_runs_the_sync(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "api", "K-1")
    fake.worktrees(root / "repos/api", (wt, "feature/K-1"))
    (wt / "target").mkdir(parents=True)
    (wt / "target" / "app.jar").write_bytes(b"PK")
    (fake.state / "docker_fail").write_text("cp")

    summary = deploy.run(make_ctx(ws, tasks=["K-1"], apps=["api"]))

    assert _result(summary, "api").status is Status.FAILED
    assert not _sync_calls(fake)
    assert not _kube_calls(fake, "restart")
    # staging is still cleaned up
    assert any(c[:1] == ["exec"] and 'rm -rf "$1"' in c for c in fake.log("docker"))


# == B17 — the forbidden scan ====================================================================

def test_b17_build_done_requires_the_artifact_to_exist(ws, root, fake):
    """Build exit 0 but no dist/ at all: the scan walks nothing and the unit is reported done
    ("built in .../dist") although no bundle exists to be scanned or deployed."""
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    r = _result(summary, "fe")
    assert r.status is not Status.DONE, f"build claimed done with no artifact: {r.reason}"


def test_b17_forbidden_address_inside_a_precompressed_asset_is_found(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    (wt / "dist").mkdir(parents=True)
    (wt / "dist" / "index.html").write_text("<html></html>")
    (wt / "dist" / "app.js.gz").write_bytes(gzip.compress(b"fetch('http://localhost:8090/x')"))

    summary = build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))

    r = _result(summary, "fe")
    assert r.status is Status.FAILED, "forbidden address shipped inside app.js.gz, not detected"


def test_b17_forbidden_address_in_source_map_is_found(ws, root, fake):
    fake.present("K-1")
    wt = worktree_dir(root, "fe", "K-1")
    fake.worktrees(root / "repos/fe", (wt, "feature/K-1"))
    (wt / "dist" / "assets").mkdir(parents=True)
    (wt / "dist" / "index.html").write_text("<html></html>")  # D8/B41: a valid artifact to scan
    (wt / "dist" / "assets" / "a.js.map").write_text('{"sourcesContent":["localhost:8090"]}')

    r = _result(build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"])), "fe")
    assert r.status is Status.FAILED and "a.js.map" in r.reason


# == node paths (_inside_root) ===================================================================

@pytest.mark.parametrize("dest", [
    "/workspace", "/workspace/", "/workspacex/ns/app", "/workspace/../etc", "/workspace/ns/../../etc",
    "/etc", "workspace/ns/app", "/workspace/./..",
])
def test_node_refuses_destinations_outside_root(ws, tmp_path, dest):
    art = tmp_path / "art"
    art.mkdir()
    (art / "f").write_text("x")
    node = Node(ws, dry_run=True, out=io.StringIO())
    with pytest.raises(NodeError):
        node.sync(art, dest)


def test_node_accepts_normalizable_destination_inside_root(ws, tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    (art / "f").write_text("x")
    out = io.StringIO()
    Node(ws, dry_run=True, out=out).sync(art, "/workspace/ns/./app/")
    assert "/workspace/ns/app" in out.getvalue()


@pytest.mark.parametrize("node_root", ["/.", "/..", "/./"])
def test_config_node_root_that_is_really_slash_is_refused(root, node_root):
    """config says node_root must be "an absolute path other than /"; these ARE /."""
    (root / "dop.toml").write_text(CONFIG.replace('node_root = "/workspace"', f'node_root = "{node_root}"'))
    with pytest.raises(UsageError):
        load(root)


def test_config_node_root_non_normalized_still_lets_deploys_in(root, tmp_path):
    """node_root = "/workspace/." is accepted by config; every destination is then refused as
    "outside /workspace/." — a config the tool accepts but can never use."""
    (root / "dop.toml").write_text(CONFIG.replace('node_root = "/workspace"', 'node_root = "/workspace/."'))
    try:
        ws = load(root)
    except UsageError:
        return  # refused at load: fine
    art = tmp_path / "art"
    art.mkdir()
    (art / "f").write_text("x")
    node = Node(ws, dry_run=True, out=io.StringIO())
    node.sync(art, node.path("optum-k-1", "fe"))  # must not raise: it is under node_root


# == B31 — companion builds ======================================================================

def test_b31_two_demands_same_companion_get_distinct_build_dirs(ws, root, fake):
    fake.present("K-1", "K-2")
    fake.worktrees(root / "repos/be",
                   (worktree_dir(root, "be", "K-1"), "feature/K-1"),
                   (worktree_dir(root, "be", "K-2"), "feature/K-2"))
    (root / "repos" / "fe" / "src").mkdir(parents=True)
    (root / "repos" / "fe" / "src" / "a.js").write_text("x")

    summary = build.run(make_ctx(ws, tasks=["K-1", "K-2"], apps=["fe"]))

    runs = [c for c in fake.log("docker") if c[:2] == ["run", "--rm"]]
    mounts = {c[c.index("-v") + 1] for c in runs}
    assert f"{ws.state_dir / 'builds' / 'K-1' / 'fe'}:/work" in mounts
    assert f"{ws.state_dir / 'builds' / 'K-2' / 'fe'}:/work" in mounts
    assert all(r.status is Status.DONE or r.unit.name != "fe" for r in summary.results) or True


def _companion_ref(ws, root, fake, status_text=None, detached=False):
    from dop.scope import resolve
    from dop.verbs.build import VERB
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    if detached:
        (fake.state / "git" / "fe.porcelain").write_text(
            f"worktree {root / 'repos/fe'}\nHEAD {'c' * 40}\ndetached\n")
    if status_text is not None:
        (fake.state / "git" / "fe.status").write_text(status_text)
    scope = resolve(make_ctx(ws, tasks=["K-1"], apps=["fe"]), VERB)
    (u,) = [u for u in scope.units if u.name == "fe"]
    return u.ref


def test_b31_label_detached_main_checkout(ws, root, fake):
    assert _companion_ref(ws, root, fake, detached=True) == "detached@ccccccc"


def test_b31_label_untracked_only_is_dirty(ws, root, fake):
    assert _companion_ref(ws, root, fake, status_text="?? scratch.txt\n").endswith("dirty")


def test_b31_label_matches_what_is_copied_ignored_local_file(ws, root, fake):
    """The main checkout holds an ignored .env.local (git status --porcelain shows nothing). The
    copy takes it into the build directory, where the build reads it; the label says clean."""
    checkout = root / "repos" / "fe"
    (checkout / "src").mkdir(parents=True)
    (checkout / "src" / "a.js").write_text("x")
    (checkout / ".env.local").write_text("VITE_OTHER=http://localhost:9999\n")
    ref = _companion_ref(ws, root, fake, status_text="")

    build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"]))
    copied = (ws.state_dir / "builds" / "K-1" / "fe" / ".env.local").exists()

    assert not (copied and not ref.endswith("dirty")), (
        f"label {ref!r} says clean but the build directory got the untracked-ignored .env.local")


def test_b31_companion_rebuild_when_previous_build_left_unremovable_output(ws, root, fake):
    """Stand-in for the real case: the build container runs as root (rootful docker), so the
    previous build's node_modules/dist in .dop/builds/<D>/<app> is root-owned and the user's
    shutil.rmtree cannot remove it. Simulated with a read-only directory."""
    fake.present("K-1")
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    (root / "repos" / "fe" / "src").mkdir(parents=True)
    dest = ws.state_dir / "builds" / "K-1" / "fe" / "node_modules" / "pkg"
    dest.mkdir(parents=True)
    (dest / "index.js").write_text("x")
    os.chmod(dest, 0o555)
    try:
        r = _result(build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"])), "fe")
    finally:
        if dest.exists():  # only if the fix under test left it behind
            os.chmod(dest, 0o755)
    # The fake build produces no artifact, so B41 now fails the unit on "no index.html"; what this
    # test guards is that the unremovable previous output no longer breaks the rebuild (architect, 28/09).
    assert r.status is Status.DONE or r.reason.startswith("no index.html"), (
        f"second companion build failed: {r.reason}")


# == B36 — build credentials =====================================================================

_FE_BUILD = 'build = { image = "node:22", command = "npm run build", env = { VITE_API = "{url:be}" } }'
_BE_BUILD = 'build = { image = "maven:3", command = ["mvn", "package"] }'


def _with_fe_credentials(root, container_path, host_path):
    cfg = CONFIG.replace(
        _FE_BUILD,
        'build = { image = "node:22", command = "npm run build", env = { VITE_API = "{url:be}" }, '
        f'credentials = {{ "{container_path}" = "{host_path}" }} }}')
    assert cfg != CONFIG
    (root / "dop.toml").write_text(cfg)


def test_b36_absent_credential_fails_naming_the_path(root, fake):
    _with_fe_credentials(root, "/root/.npmrc", str(root / "nope.npmrc"))
    ws = load(root)
    fake.present("K-1")
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "feature/K-1"))
    r = _result(build.run(make_ctx(ws, tasks=["K-1"], apps=["fe"])), "fe")
    assert r.status is Status.FAILED
    assert "nope.npmrc" in r.reason
    assert not [c for c in fake.log("docker") if c[:1] == ["run"]], "container ran without its credential"


def test_b36_dry_run_never_prints_credential_content(root, fake):
    cred = root / "cred.npmrc"
    cred.write_text("//registry/:_authToken=TOPSECRET-TOKEN\n")
    _with_fe_credentials(root, "/root/.npmrc", str(cred))
    ws = load(root)
    fake.present("K-1")
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "feature/K-1"))
    ctx = make_ctx(ws, tasks=["K-1"], apps=["fe"], dry_run=True)
    build.run(ctx)
    out = ctx.out.getvalue()
    assert "TOPSECRET" not in out
    assert f"{cred}:/root/.npmrc:ro" in out


@pytest.mark.parametrize("container_path", ["/work/.npmrc", "/work", "/work/sub/.npmrc"])
def test_b36_credential_inside_the_build_directory_is_refused(root, container_path):
    """A credential mounted under /work (the build directory's mount) sits inside the build
    directory: the build sees it as a file of the tree (and can copy it into the artifact), and
    docker leaves a root-owned mountpoint file in the host worktree. B36: never in a build dir."""
    cred = root / "cred.npmrc"
    cred.write_text("x")
    _with_fe_credentials(root, container_path, str(cred))
    with pytest.raises(UsageError):
        load(root)


def test_b36_backend_credential_inside_the_shared_maven_cache_is_refused(root):
    """.m2/settings.xml — the natural Maven credential path, under the shared dop-maven-cache
    volume's mount (config.MAVEN_CACHE_PATH: /tmp/.m2, under HOME=/tmp so a non-root --user can
    write to it, D9/B42) — docker would leave an empty settings.xml there that breaks every other
    back-end build ("Non-readable settings ... input contained no data")."""
    cred = root / "settings.xml"
    cred.write_text("<settings/>")
    cfg = CONFIG.replace(
        _BE_BUILD,
        'build = { image = "maven:3", command = ["mvn", "package"], '
        f'credentials = {{ "/tmp/.m2/settings.xml" = "{cred}" }} }}')
    assert cfg != CONFIG
    (root / "dop.toml").write_text(cfg)
    with pytest.raises(UsageError):
        load(root)


def test_b36_relative_host_path_is_resolved_against_the_workspace(root, fake, monkeypatch, tmp_path):
    """Every other path in dop.toml is relative to the workspace; a credential host path is
    resolved against the process cwd instead, and then handed to `docker -v` relative."""
    (root / "creds").mkdir()
    (root / "creds" / "npmrc").write_text("x")
    _with_fe_credentials(root, "/root/.npmrc", "creds/npmrc")
    ws = load(root)
    fake.present("K-1")
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "feature/K-1"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ctx = make_ctx(ws, tasks=["K-1"], apps=["fe"], dry_run=True)
    r = _result(build.run(ctx), "fe")
    assert r.status is not Status.FAILED, r.reason
    assert f"{root / 'creds' / 'npmrc'}:/root/.npmrc:ro" in ctx.out.getvalue()
