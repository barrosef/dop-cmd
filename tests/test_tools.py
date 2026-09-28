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


SYNC_SH = pytest.mark.skipif(not os.path.exists("/bin/sh"), reason="needs /bin/sh")


@SYNC_SH
def test_sync_script_makes_contents_exactly_equal(tmp_path):
    """The in-node script, run locally: stale entries go, type changes are handled, the directory
    itself (its inode) stays."""
    from dop.node import _SYNC_SCRIPT

    src, dest = tmp_path / "src", tmp_path / "dest"
    (src / "assets").mkdir(parents=True)
    (src / "index.html").write_text("new")
    (src / "assets" / "a.js").write_text("a")
    (src / "was-file").mkdir()
    (src / "was-file" / "x").write_text("x")
    (dest / "assets").mkdir(parents=True)
    (dest / "index.html").write_text("old")
    (dest / "stale.js").write_text("s")
    (dest / "assets" / "stale.js").write_text("s")
    (dest / "was-file").write_text("f")
    inode = dest.stat().st_ino
    subprocess.run(["sh", "-c", _SYNC_SCRIPT, "sh", str(src), str(dest)], check=True)
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*"))
    assert got == ["assets", "assets/a.js", "index.html", "was-file", "was-file/x"]
    assert (dest / "index.html").read_text() == "new"
    assert dest.stat().st_ino == inode


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
