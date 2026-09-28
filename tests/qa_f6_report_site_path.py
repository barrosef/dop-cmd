"""QA F6 — `dop report`'s printed address vs where `node.sync` actually places content (B16, B23).

Oracle: docs/business-rules.md B16, B21-B23; "Step-2 choices accepted by the architect" (reports
synced to `<node_root>/<shared_namespace>/reports/<DEMAND>/<project>/`).
Scope: FRONT (test report verbs) — src/dop/verbs/_testing.py (regenerate_report), verbs/report.py.
Never writes to the real workspace; uses the `root`/`ws`/`fake` fixtures (scratch tmp_path).
"""

from __future__ import annotations

from pathlib import Path

from dop.verbs import registry
from dop.verbs._testing import regenerate_report

from .conftest import make_ctx, worktree_dir


def _run(ctx, verb_name):
    return registry()[verb_name].run(ctx)


def _aaa_project(root: Path, repo: str) -> Path:
    proj = root / "test" / "aaa" / repo
    proj.mkdir(parents=True)
    (proj / "pom.xml").write_text("<project/>")
    return proj


def test_report_url_site_suffix_does_not_match_node_sync_destination(root, ws, fake):
    """B23: `regenerate_report` runs `allure generate -o site` on the host (so the host copy of a
    project's report legitimately has a `site/` subdirectory), then syncs that `site/` directory's
    *contents* straight into `<node_root>/<shared_namespace>/reports/<DEMAND>/<project>/` on the
    node -- with no `/site` segment in the node path (`node.sync(project_dir / "site", node.path(...,
    "reports", demand, project))` copies `site`'s contents, it does not create a nested `site/` at
    the destination).

    `dop report` (verbs/report.py `_report`) prints `{base}/{demand}/{project}/site/` -- built by
    checking the HOST's `site/` directory exists, then appending `/site/` to the URL. That extra
    path segment has nothing behind it on the node/nginx side: the address `dop report` hands the
    operator does not correspond to where the content was actually copied.
    """
    _aaa_project(root, "api")
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))

    # Pre-seed a completed run + a generated site, exactly the state `run_repo_layer` leaves behind
    # (B16: runs are never pruned, so re-reading an existing run-1/site is a realistic starting
    # point, not a shortcut around the code under test).
    run_dir = root / "test/e2e/reports/K-1/aaa-api/run-1/results"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text("{}")
    site_dir = root / "test/e2e/reports/K-1/aaa-api/site"
    site_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<html/>")

    ctx = make_ctx(ws, tasks=["K-1"], repos=["api"])
    warning = regenerate_report(ws, ctx.runner, ctx.node, "K-1", "aaa-api")
    assert warning is None, f"unexpected report regeneration warning: {warning}"

    # Where the sync actually landed the content in the node (captured from the fake docker's
    # exec log; the sync script is the only exec call containing "cp -a").
    sync_calls = [a for a in fake.log("docker") if a[:1] == ["exec"] and "cp -a" in a[4]]
    assert len(sync_calls) == 1, f"expected exactly one sync exec call, got {sync_calls}"
    node_dest = sync_calls[0][-1]
    assert node_dest == "/workspace/optum-shared/reports/K-1/aaa-api", (
        f"sanity check on the node destination itself failed: {node_dest}"
    )

    report_ctx = make_ctx(ws, tasks=["K-1"])
    _run(report_ctx, "report")
    printed = report_ctx.out.getvalue().strip()
    assert printed == "http://reports.localhost:8080/K-1/aaa-api/site/", (
        f"sanity check on the printed URL format itself failed: {printed!r}"
    )

    # THE DEFECT: the path `report` prints, translated into what nginx would actually be asked
    # for, must equal where the content was actually placed on the node. It does not -- `report`
    # prints an address with a trailing `/site/` that the node copy never created.
    printed_path_after_host = printed.split("localhost:8080", 1)[1]  # "/K-1/aaa-api/site/"
    served_path = f"/optum-shared/reports{printed_path_after_host}".rstrip("/")
    node_path = node_dest[len("/workspace"):]  # "/optum-shared/reports/K-1/aaa-api"
    assert served_path == node_path, (
        f"`dop report` prints {printed!r}; nginx serving node_root={node_dest.rsplit('/optum-shared',1)[0]!r} "
        f"would look for {served_path!r}, but the site was synced to {node_path!r}. Following the "
        f"printed URL 404s; the real content is one path segment up (no /site/)."
    )
