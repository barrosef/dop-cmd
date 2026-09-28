"""`dop report --publish` (B23, B44) and `Node.put`, the operation it needs (B16 amended)."""

from __future__ import annotations

import io
import subprocess

import pytest

from dop.context import Context, Filters
from dop.node import Node, NodeError
from dop.outcome import Status
from dop.verbs import registry

from .conftest import make_ctx


def _publish_ctx(ws, *, dry_run=False) -> Context:
    return Context(ws=ws, filters=Filters(), dry_run=dry_run, out=io.StringIO(),
                   options={"publish": True})


def _run(ctx):
    return registry()["report"].run(ctx)


def _by_name(summary):
    return {r.unit.name: r for r in summary.results}


def _make_project(ws, name: str, *files: str) -> None:
    d = ws.paths.reports / name
    d.mkdir(parents=True)
    (d / "index.html").write_text(f"<title>{name}</title>")
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("x")


# ---------------------------------------------------------------------------------------------
# scope: which top-level entries are published


def test_publish_nothing_to_publish_is_a_partial_note(ws, fake):
    summary = _run(_publish_ctx(ws))
    assert summary.results == []
    assert summary.note == "no report site to publish"
    assert summary.exit_code() == 3


def test_publish_syncs_each_project_directory_with_its_own_index_html(ws, fake):
    _make_project(ws, "aaa-api", "widgets/charts.json")
    _make_project(ws, "it-be")
    # a raw per-demand results tree: no index.html at its own top level -- never published
    (ws.paths.reports / "K-1" / "aaa-api" / "run-1" / "results").mkdir(parents=True)
    (ws.paths.reports / "K-1" / "aaa-api" / "run-1" / "results" / "x.json").write_text("{}")

    summary = _run(_publish_ctx(ws))
    results = _by_name(summary)
    assert set(results) == {"aaa-api", "it-be"}
    assert all(r.status is Status.DONE for r in results.values())

    calls = fake.log("docker")
    cp_srcs = [c[1] for c in calls if c[0] == "cp"]
    assert any(str(ws.paths.reports / "aaa-api") in s for s in cp_srcs)
    assert any(str(ws.paths.reports / "it-be") in s for s in cp_srcs)
    assert not any("K-1" in s for s in cp_srcs)

    exec_calls = [c for c in calls if c[0] == "exec"]
    dests = {c[-1] for c in exec_calls if c[-1].startswith("/workspace/optum-shared/reports/")}
    assert dests == {
        "/workspace/optum-shared/reports/aaa-api",
        "/workspace/optum-shared/reports/it-be",
    }


def test_publish_one_project_failing_does_not_stop_the_other(ws, fake):
    _make_project(ws, "aaa-api")
    _make_project(ws, "it-be")
    (fake.state / "docker_fail").write_text("cp")

    summary = _run(_publish_ctx(ws))
    results = _by_name(summary)
    assert results["aaa-api"].status is Status.FAILED
    assert results["it-be"].status is Status.FAILED


# ---------------------------------------------------------------------------------------------
# the landing page: staged apart, put in overwriting only files of the same name


def test_publish_landing_page_stages_only_root_files(ws, fake):
    _make_project(ws, "aaa-api")
    (ws.paths.reports / "index.html").write_text("<html>landing</html>")
    (ws.paths.reports / "logo.png").write_bytes(b"\x89PNG")

    summary = _run(_publish_ctx(ws))
    results = _by_name(summary)
    assert results["landing"].status is Status.DONE

    staged = ws.state_dir / "publish" / "landing"
    assert sorted(p.name for p in staged.iterdir()) == ["index.html", "logo.png"]
    assert (staged / "index.html").read_text() == "<html>landing</html>"
    assert (staged / "logo.png").read_bytes() == b"\x89PNG"

    calls = fake.log("docker")
    exec_calls = [c for c in calls if c[0] == "exec"]
    put_dest = exec_calls[2][-1] if len(exec_calls) > 2 else None
    assert any(c[-1] == "/workspace/optum-shared/reports" for c in exec_calls)


def test_publish_no_root_files_skips_the_landing_unit(ws, fake):
    _make_project(ws, "aaa-api")
    summary = _run(_publish_ctx(ws))
    assert "landing" not in _by_name(summary)


def test_publish_dry_run_writes_nothing_and_reports_size(ws, fake):
    _make_project(ws, "aaa-api", "widgets/charts.json")
    (ws.paths.reports / "index.html").write_text("<html>landing</html>")

    summary = _run(_publish_ctx(ws, dry_run=True))
    results = _by_name(summary)
    assert results["aaa-api"].status is Status.PLANNED
    assert "bytes" in results["aaa-api"].reason
    assert results["landing"].status is Status.PLANNED
    assert "bytes" in results["landing"].reason

    assert fake.log("docker") == []
    assert not (ws.state_dir / "publish").exists()


def test_publish_takes_no_lock(ws, fake):
    """B27 amended: `report` is read-only, `--publish` included -- two runs never contend."""
    _make_project(ws, "aaa-api")
    first = _run(_publish_ctx(ws))
    second = _run(_publish_ctx(ws))
    assert _by_name(first)["aaa-api"].status is Status.DONE
    assert _by_name(second)["aaa-api"].status is Status.DONE


# ---------------------------------------------------------------------------------------------
# Node.put (B44): overwrites only entries of the same name, never removes anything else


def test_put_stages_then_runs_the_put_script_not_sync_or_seed(ws, fake, tmp_path):
    from dop.node import _PUT_SCRIPT

    src = tmp_path / "landing"
    src.mkdir()
    (src / "index.html").write_text("x")
    (src / "logo.png").write_bytes(b"\x89PNG")

    Node(ws, dry_run=False, out=io.StringIO()).put(src, "/workspace/optum-shared/reports")
    calls = fake.log("docker")
    assert [c[0] for c in calls] == ["exec", "cp", "exec", "exec", "exec"]
    assert calls[3][-4:] == [_PUT_SCRIPT, "sh", calls[0][-1], "/workspace/optum-shared/reports"]


def test_put_refuses_paths_outside_root(ws, fake, tmp_path):
    src = tmp_path / "landing"
    src.mkdir()
    (src / "index.html").write_text("x")
    with pytest.raises(NodeError, match="outside"):
        Node(ws, dry_run=False, out=io.StringIO()).put(src, "/etc/reports")
    assert fake.log("docker") == []


def test_put_absent_or_empty_source_touches_nothing(ws, fake, tmp_path):
    n = Node(ws, dry_run=False, out=io.StringIO())
    with pytest.raises(NodeError, match="absent"):
        n.put(tmp_path / "nope", "/workspace/optum-shared/reports")
    (tmp_path / "empty").mkdir()
    with pytest.raises(NodeError, match="empty"):
        n.put(tmp_path / "empty", "/workspace/optum-shared/reports")
    assert fake.log("docker") == []


def test_put_dry_run_prints(ws, fake, tmp_path):
    src = tmp_path / "landing"
    src.mkdir()
    (src / "index.html").write_text("x")
    out = io.StringIO()
    Node(ws, dry_run=True, out=out).put(src, "/workspace/optum-shared/reports")
    assert fake.log("docker") == []
    assert "[dry-run] docker cp" in out.getvalue()


# The script itself deletes nothing and never parses names; run only in a throwaway sandbox
# container of the node's own image, exactly as node.py's own sync/seed script tests do.
NODE_IMAGE = "rancher/k3s:v1.35.5-k3s1"


def _node_image_available() -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", NODE_IMAGE],
                               capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


IN_SANDBOX = pytest.mark.skipif(not _node_image_available(), reason=f"needs docker and {NODE_IMAGE}")


def _sandbox(setup: str, script: str, *args: str, after: str = "") -> subprocess.CompletedProcess:
    driver = f'{setup}\nsh -c "$DOP_SCRIPT" sh "$@"; echo "rc=$?"\n{after}\n'
    return subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--read-only",
         "--tmpfs", "/workspace", "--tmpfs", "/outside", "-e", f"DOP_SCRIPT={script}",
         "--entrypoint", "/bin/sh", NODE_IMAGE, "-c", driver, "sh", *args],
        capture_output=True, text=True, timeout=60)


@IN_SANDBOX
def test_put_script_overwrites_only_same_named_entries():
    from dop.node import _PUT_SCRIPT

    setup = r"""
    set -e
    mkdir -p /workspace/st /workspace/d/aaa-api
    echo new > /workspace/st/index.html; echo p > /workspace/st/logo.png
    echo old > /workspace/d/index.html; echo r > /workspace/d/aaa-api/x
    set +e
    """
    after = r"""cd /workspace/d && find . -mindepth 1 | sort | tr '\n' ' '; echo
    cat index.html; cat aaa-api/x"""
    proc = _sandbox(setup, _PUT_SCRIPT, "/workspace/st", "/workspace/d", after=after)
    out = proc.stdout.splitlines()
    assert "rc=0" in out, proc.stderr
    assert "./aaa-api ./aaa-api/x ./index.html ./logo.png " in out
    assert out[-2] == "new"
    assert out[-1] == "r"


@IN_SANDBOX
def test_put_script_creates_the_destination_when_absent():
    from dop.node import _PUT_SCRIPT

    setup = "mkdir -p /workspace/st; echo x > /workspace/st/index.html"
    after = "cat /workspace/new/reports/index.html"
    proc = _sandbox(setup, _PUT_SCRIPT, "/workspace/st", "/workspace/new/reports", after=after)
    out = proc.stdout.splitlines()
    assert out == ["rc=0", "x"], proc.stderr
