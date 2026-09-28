"""kube (B15, B25), node (B6, B16, B24), lock (B27, B34), runner (B21, B30, B35), address (B3)."""

import io
import json
import os
import socket
import subprocess

import pytest

from dop.address import address, expand, host, namespace
from dop.kube import Kube, KubeError
from dop.lock import LockHeld, lock, lock_name
from dop.node import Node, NodeError
from dop.outcome import Status, Unit, UsageError, done
from dop.runner import Mount, Runner
from dop.verbs import act

from .conftest import make_ctx

# -- kube ---------------------------------------------------------------------------------------

def test_kube_mutation_in_dry_run_is_printed_not_run(ws, fake):
    out = io.StringIO()
    k = Kube(ws, dry_run=True, out=out)
    k.run(["apply", "-k", "/x"])
    assert "[dry-run] kubectl --context k3d-test apply -k /x" in out.getvalue()
    assert not any("apply" in c for c in fake.log("kubectl"))


def test_kube_read_runs_in_dry_run(ws, fake):
    fake.present("K-1")
    k = Kube(ws, dry_run=True, out=io.StringIO())
    data = k.json(["get", "namespaces"])
    assert data["items"][0]["metadata"]["name"] == "optum-k-1"


def test_kube_failure_raises(ws, fake):
    fake.kube_fail()
    with pytest.raises(KubeError, match="refused"):
        Kube(ws, False, io.StringIO()).run(["get", "pods"])


def test_kube_unknown_context_is_usage_error(ws, fake):
    fake.contexts("client-prod")
    with pytest.raises(UsageError, match="k3d-test"):
        Kube(ws, False, io.StringIO()).run(["get", "pods"], read=True)


# -- node ---------------------------------------------------------------------------------------

def test_sync_absent_or_empty_source_touches_nothing(ws, fake, tmp_path):
    n = Node(ws, dry_run=False, out=io.StringIO())
    with pytest.raises(NodeError, match="absent"):
        n.sync(tmp_path / "nope", "/workspace/optum-k-1/api")
    (tmp_path / "empty").mkdir()
    with pytest.raises(NodeError, match="empty"):
        n.sync(tmp_path / "empty", "/workspace/optum-k-1/api")
    assert fake.log("docker") == []


def test_sync_stages_verifies_then_replaces_in_place(ws, fake, tmp_path):
    src = tmp_path / "dist"
    src.mkdir()
    (src / "index.html").write_text("x")
    Node(ws, dry_run=False, out=io.StringIO()).sync(src, "/workspace/optum-k-1/fe")
    calls = fake.log("docker")
    assert [c[0] for c in calls] == ["exec", "cp", "exec", "exec", "exec"]
    staging = calls[0][-1]
    assert staging.startswith("/workspace/.staging/")
    assert calls[1] == ["cp", f"{src}/.", f"k3d-test-server-0:{staging}"]
    assert calls[3][-2:] == [staging, "/workspace/optum-k-1/fe"]  # sync script: src, dest
    assert calls[4][-1] == staging  # staging removed


def test_sync_empty_staging_leaves_destination(ws, fake, tmp_path):
    src = tmp_path / "dist"
    src.mkdir()
    (src / "a").write_text("x")
    (fake.state / "docker_staged_empty").write_text("1")
    with pytest.raises(NodeError, match="left as it was"):
        Node(ws, dry_run=False, out=io.StringIO()).sync(src, "/workspace/optum-k-1/fe")
    calls = fake.log("docker")
    assert not any("/workspace/optum-k-1/fe" in c for c in calls)
    assert calls[-1][-1].startswith("/workspace/.staging/")  # staging still cleaned up


def test_sync_dry_run_prints(ws, fake, tmp_path):
    src = tmp_path / "dist"
    src.mkdir()
    (src / "a").write_text("x")
    out = io.StringIO()
    Node(ws, dry_run=True, out=out).sync(src, "/workspace/optum-k-1/fe")
    assert fake.log("docker") == []
    assert "[dry-run] docker cp" in out.getvalue()


def test_node_refuses_paths_outside_root(ws, fake):
    n = Node(ws, dry_run=False, out=io.StringIO())
    for bad in ("/etc", "/workspace", "/workspace/../etc", "/workspacex/a"):
        with pytest.raises(NodeError, match="outside"):
            n.remove(bad)
    assert fake.log("docker") == []


# The in-node scripts delete things. They are NEVER run on the host: only inside a throwaway
# container of the node's own image (BusyBox sh, GNU cp/find), read-only root, no network, every
# writable path a tmpfs that dies with it. No docker or no image: skipped.
NODE_IMAGE = "rancher/k3s:v1.35.5-k3s1"


def _node_image_available() -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", NODE_IMAGE],
                              capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


IN_SANDBOX = pytest.mark.skipif(not _node_image_available(), reason=f"needs docker and {NODE_IMAGE}")


def _sandbox(setup: str, script: str, *args: str, after: str = "") -> subprocess.CompletedProcess:
    """setup; then `script` with positional args; then `after` — all in one sandboxed container.
    Prints `rc=<script's exit>` between the script and `after`."""
    driver = f'{setup}\nsh -c "$DOP_SCRIPT" sh "$@"; echo "rc=$?"\n{after}\n'
    return subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--tmpfs", "/workspace", "--tmpfs", "/outside", "-e", f"DOP_SCRIPT={script}",
         "--entrypoint", "/bin/sh", NODE_IMAGE, "-c", driver, "sh", *args],
        capture_output=True, text=True, timeout=60)


@IN_SANDBOX
def test_sync_script_makes_contents_exactly_equal():
    """Stale entries go, type changes are handled, the directory itself (its inode) stays."""
    from dop.node import _SYNC_SCRIPT

    setup = r"""
    set -e
    mkdir -p /workspace/st/assets /workspace/st/was-file /workspace/d/assets
    echo new > /workspace/st/index.html; echo a > /workspace/st/assets/a.js; echo x > /workspace/st/was-file/x
    echo old > /workspace/d/index.html; echo s > /workspace/d/stale.js; echo s > /workspace/d/assets/stale.js
    echo f > /workspace/d/was-file
    stat -c 'inode=%i' /workspace/d
    set +e
    """
    after = r"""stat -c 'inode=%i' /workspace/d
    cd /workspace/d && find . -mindepth 1 | sort | tr '\n' ' '; echo; cat index.html"""
    proc = _sandbox(setup, _SYNC_SCRIPT, "/workspace/st", "/workspace/d", after=after)
    out = proc.stdout.splitlines()
    assert "rc=0" in out, proc.stderr
    inodes = [line for line in out if line.startswith("inode=")]
    assert len(inodes) == 2 and inodes[0] == inodes[1]
    assert "./assets ./assets/a.js ./index.html ./was-file ./was-file/x " in out
    assert out[-1] == "new"


@IN_SANDBOX
def test_sync_script_never_parses_names_nor_follows_symlinks():
    """D1/D2: names with a newline, a leading dash or a glob are removed as themselves; symlinks in
    the destination are removed as links, never followed; symlinks in the artifact are copied as
    links. Nothing outside the destination changes."""
    from dop.node import _SYNC_SCRIPT

    setup = r"""
    set -e
    mkdir -p /workspace/st/assets /workspace/d/assets
    echo keep > /outside/keep; mkdir /outside/sub; echo keep > /outside/sub/f
    echo new > /workspace/st/index.html; ln -s ../index.html /workspace/st/assets/link
    nl='
'
    touch "/workspace/d/evil${nl}..${nl}outside" /workspace/d/-rf "/workspace/d/ *"
    ln -s /outside /workspace/d/assets/out; ln -s /outside/keep /workspace/d/index.html
    ln -s /outside /workspace/d/stale
    set +e
    """
    after = r"""cd /workspace/d && find . -mindepth 1 | sort | tr '\n' ' '; echo
    readlink /workspace/d/assets/link; find /outside | sort | tr '\n' ' '; echo; cat /outside/keep"""
    proc = _sandbox(setup, _SYNC_SCRIPT, "/workspace/st", "/workspace/d", after=after)
    out = proc.stdout.splitlines()
    assert "rc=0" in out, proc.stderr
    assert "./assets ./assets/link ./index.html " in out
    assert "../index.html" in out
    assert "/outside /outside/keep /outside/sub /outside/sub/f " in out
    assert out[-1] == "keep"


@IN_SANDBOX
@pytest.mark.parametrize("dest", ["/workspace/dl", "/workspace/anc/app"])
def test_sync_script_refuses_a_destination_reached_through_a_symlink(dest):
    from dop.node import _SYNC_SCRIPT

    setup = r"""
    mkdir -p /workspace/st; echo new > /workspace/st/index.html; echo keep > /outside/keep
    ln -s /outside /workspace/dl; ln -s /outside /workspace/anc
    """
    proc = _sandbox(setup, _SYNC_SCRIPT, "/workspace/st", dest, after="ls /outside")
    out = proc.stdout.splitlines()
    assert "rc=0" not in out
    assert "resolves elsewhere" in proc.stderr
    assert out[-1] == "keep"


@IN_SANDBOX
def test_check_staged_rejects_a_file_less_tree():
    from dop.node import _CHECK_STAGED

    proc = _sandbox("mkdir -p /workspace/e/sub /workspace/f/sub; echo x > /workspace/f/sub/a",
                    _CHECK_STAGED, "/workspace/e", after=r"""sh -c "$DOP_SCRIPT" sh /workspace/f""")
    out = proc.stdout.splitlines()
    assert out[0] == "rc=1" and out[1] == "sub"


@IN_SANDBOX
def test_seed_script_creates_the_page_only_where_there_is_none():
    """D7: env up's seed never removes nor overwrites what is already served."""
    from dop.node import _SEED_SCRIPT

    setup = r"""
    mkdir -p /workspace/sd /workspace/rep/K-1/aaa; echo placeholder > /workspace/sd/index.html
    echo published > /workspace/rep/index.html; echo r > /workspace/rep/K-1/aaa/x
    """
    after = r"""cat /workspace/rep/index.html; ls /workspace/rep/K-1/aaa
    sh -c "$DOP_SCRIPT" sh /workspace/sd /workspace/new/reports && cat /workspace/new/reports/index.html"""
    proc = _sandbox(setup, _SEED_SCRIPT, "/workspace/sd", "/workspace/rep", after=after)
    assert proc.stdout.splitlines() == ["rc=0", "published", "x", "placeholder"], proc.stderr


def test_sync_file_less_source_touches_nothing(ws, fake, tmp_path):
    """Only empty directories: nothing to serve, nothing sent to the node (B16)."""
    (tmp_path / "target" / "classes").mkdir(parents=True)
    with pytest.raises(NodeError, match="empty"):
        Node(ws, dry_run=False, out=io.StringIO()).sync(tmp_path / "target", "/workspace/optum-k-1/api")
    assert fake.log("docker") == []


def test_seed_stages_then_runs_the_seed_script_not_the_sync(ws, fake, tmp_path):
    from dop.node import _SEED_SCRIPT

    src = tmp_path / "seed"
    src.mkdir()
    (src / "index.html").write_text("x")
    Node(ws, dry_run=False, out=io.StringIO()).seed(src, "/workspace/optum-shared/reports")
    calls = fake.log("docker")
    assert [c[0] for c in calls] == ["exec", "cp", "exec", "exec", "exec"]
    assert calls[3][-4:] == [_SEED_SCRIPT, "sh", calls[0][-1], "/workspace/optum-shared/reports"]
    assert "rm " not in _SEED_SCRIPT


# -- lock ---------------------------------------------------------------------------------------

def test_second_writer_fails_naming_holder(tmp_path):
    u = Unit("app", "K-1", "api")
    with lock(tmp_path, u, command="dop deploy"):
        with pytest.raises(LockHeld, match=f"pid {os.getpid()}.*dop deploy"):
            with lock(tmp_path, u):
                pass
    assert not (tmp_path / lock_name(u)).exists()


def test_different_units_do_not_block(tmp_path):
    with lock(tmp_path, Unit("app", "K-1", "api")):
        with lock(tmp_path, Unit("app", "K-1", "be")):
            with lock(tmp_path, Unit("app", "K-2", "api")):
                pass


def test_stale_lock_is_broken_and_reported(tmp_path):
    u = Unit("app", "K-1", "api")
    dead = subprocess.Popen(["true"])
    dead.wait()
    (tmp_path / lock_name(u)).write_text(json.dumps(
        {"pid": dead.pid, "host": socket.gethostname(), "command": "dop build"}))
    out = io.StringIO()
    with lock(tmp_path, u, out=out):
        pass
    assert f"broke stale lock of dead pid {dead.pid}" in out.getvalue()


def test_lock_of_other_host_is_not_broken(tmp_path):
    u = Unit("app", "K-1", "api")
    (tmp_path / lock_name(u)).write_text(json.dumps({"pid": 1, "host": "elsewhere"}))
    with pytest.raises(LockHeld, match="elsewhere"):
        with lock(tmp_path, u):
            pass


def test_act_fails_only_the_locked_unit(ws):
    ctx = make_ctx(ws)
    a, b = Unit("app", "K-1", "api"), Unit("app", "K-1", "be")
    with lock(ws.state_dir / "locks", a):
        ra = act(ctx, a, lambda: done(a))
    rb = act(ctx, b, lambda: done(b))
    assert ra.status is Status.FAILED and "locked" in ra.reason
    assert rb.status is Status.DONE


def test_act_turns_exceptions_into_failed(ws):
    ctx = make_ctx(ws)
    u = Unit("app", "K-1", "api")

    def boom():
        raise RuntimeError("kaput")

    r = act(ctx, u, boom)
    assert r.status is Status.FAILED and "kaput" in r.reason


# -- runner -------------------------------------------------------------------------------------

def test_runner_never_puts_env_values_on_the_command_line(fake):
    out = io.StringIO()
    Runner(dry_run=True, out=out).run(
        "maven:3", [Mount("/w", "/src", True)], {"DB_PASSWORD": "s3cret"}, "host", "/src",
        ["mvn", "test"], add_hosts={"fe.k-1.localhost": "127.0.0.1"},
    )
    text = out.getvalue()
    assert "s3cret" not in text
    assert "-e DB_PASSWORD" in text and "--network host" in text
    assert "--add-host fe.k-1.localhost:127.0.0.1" in text and "-v /w:/src:ro" in text
    assert fake.log("docker") == []


def test_runner_runs_docker(fake):
    proc = Runner(False, io.StringIO()).run("img", [], {"K": "v"}, None, None, ["true"], capture=True)
    assert proc.returncode == 0
    assert fake.log("docker") == [["run", "--rm", "-e", "K", "img", "true"]]


# -- address (B3) -------------------------------------------------------------------------------

def test_addresses(ws):
    assert namespace(ws, "SUOPT-1530") == "optum-suopt-1530"
    assert host(ws, "SUOPT-1530", "fe") == "fe.suopt-1530.localhost"
    assert address(ws, "SUOPT-1530", "fe") == "http://fe.suopt-1530.localhost:8080"
    assert address(ws, None, "reports") == "http://reports.localhost:8080"
    assert address(ws, "SUOPT-1530", "be", "service") == "http://be:8090"


def test_expand(ws):
    fe = ws.apps["fe"]
    assert expand("{url:be}/api", ws, "K-1") == "http://be.k-1.localhost:8080/api"
    assert expand("{url:be}", ws, "K-1", apps={"fe", "be"}, fallbacks=fe.fallbacks()) == "http://be.k-1.localhost:8080"
    assert expand("{url:be}", ws, "K-1", apps={"fe"}, fallbacks=fe.fallbacks()) == "https://be.remote.example"
    with pytest.raises(UsageError):
        expand("{url:api}", ws, "K-1", apps={"fe"}, fallbacks=fe.fallbacks())
    with pytest.raises(UsageError):
        expand("{url:ghost}", ws, "K-1")
