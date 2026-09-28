"""QA (tester F2) — front: contract command shares. B13, B14, B15, B16 (exit-code/dry-run angle
only: B16's "nothing replaces a running artifact before verified" is covered here to the extent
exit codes/dry-run show it; "nothing deletes a report run" belongs to another tester's slice).

Oracle: docs/business-rules.md (branch k3s). HEAD at authoring time: 1d385ae, clean.

Scope: src/dop/cli.py, src/dop/outcome.py, src/dop/verbs/__init__.py, and each verb's run() only
as far as the shared exit-code/dry-run contract goes. Not a re-test of each verb's own business
logic (build/deploy/status/etc. have their own suites already).

A test red here because of a product defect stays red (see COMMON.md / AGENTS.md). Two are red on
purpose: test_up_dry_run_writes_overlay_and_secret_to_disk and
test_env_up_dry_run_seeds_reports_dir_on_disk. Do not "fix" them to pass.
"""

from __future__ import annotations

import pytest

from .conftest import run_cli, worktree_dir


def _seed_manifests(ws, *apps: str) -> None:
    """Same recipe as test_verbs_2a.py's helper: render_demand only needs the base dir to exist."""
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        app = ws.apps[name]
        (ws.paths.manifests / "demand" / kind_dir[app.kind] / name).mkdir(parents=True, exist_ok=True)


# =================================================================================================
# DEFECT 1 — `dop up --dry-run` writes the real overlay (and a real secrets file) to disk.
#
# B15: "--dry-run … resolves the same units, prints what would be done to each, changes nothing".
# verbs/up.py calls render.render_demand() unconditionally, *before* checking ctx.dry_run — unlike
# every other mutation in this codebase (Kube.run, Node.sync/_docker, Runner.run, _testing.stage),
# render_demand() takes no dry_run parameter at all and is a plain, unguarded set of filesystem
# writes: it rmtree()s any existing .dop/overlays/<DEMAND>/, recreates it, and writes
# namespace.yaml, kustomization.yaml and a 0600 secret.env file carrying the *actual* secret
# values read from the workspace env file (B30: "never printed, logged or put in a report" — this
# is none of those three, but it is an unconditional disk write of secret material that a run
# explicitly asked to change nothing should never have produced).
# =================================================================================================

def test_up_dry_run_writes_overlay_and_secret_to_disk(ws, root, fake):
    fake.present()  # nothing present yet — up must still resolve to enter it
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    _seed_manifests(ws, "be", "fe")  # "be"'s companion is "fe" (B19); secrets = [DB_PASSWORD, MAIL_PASSWORD]

    overlay = ws.state_dir / "overlays" / "K-1"
    assert not overlay.exists(), "setup sanity: overlay must not pre-exist"

    code, out = run_cli(root, "up", "--tasks", "K-1", "--dry-run")

    assert code == 0  # B15: dry-run's planned units count as done
    assert "[dry-run]" not in out or True  # not the point of this test; see assertions below

    # B15 — a run that "changes nothing" must not leave a real overlay directory behind.
    assert not overlay.exists(), (
        f"dop up --dry-run wrote a real overlay to {overlay} "
        f"(render_demand() ignores ctx.dry_run entirely)"
    )


def test_up_dry_run_does_not_leak_secret_values_to_disk(ws, root, fake):
    """Sharper version of the same defect: even if one argues the overlay skeleton is harmless to
    pre-render, the *secret file* with real passwords must never touch disk under --dry-run."""
    fake.present()
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    _seed_manifests(ws, "be", "fe")

    secret_file = ws.state_dir / "overlays" / "K-1" / "secret.env"

    run_cli(root, "up", "--tasks", "K-1", "--dry-run")

    assert not secret_file.exists(), (
        f"dop up --dry-run wrote real secret values to {secret_file}: "
        f"{secret_file.read_text() if secret_file.exists() else ''!r}"
    )


def test_up_dry_run_control_real_run_does_write_the_overlay(ws, root, fake):
    """Control for the two defects above: proves the assertion targets real behaviour, not a
    fixture quirk — a non-dry-run `up` is expected and correct to write the overlay."""
    fake.present()
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    _seed_manifests(ws, "be", "fe")

    code, _ = run_cli(root, "up", "--tasks", "K-1")

    assert code == 0
    overlay = ws.state_dir / "overlays" / "K-1"
    assert overlay.exists()
    assert (overlay / "secret.env").exists()  # real run: this one is supposed to be there


# =================================================================================================
# DEFECT 2 — `dop env up --dry-run` seeds the real reports placeholder on disk.
#
# Same shape of bug, different verb: verbs/env_up.py's _seed_reports_dir() does a plain
# `staging.mkdir(...)` + `write_text(...)` under `.dop/seed/reports/index.html` before ever
# touching ctx.node.sync() (which *is* dry-run aware for its docker layer). The write is not
# behind any `if ctx.dry_run` check.
# =================================================================================================

def test_env_up_dry_run_seeds_reports_dir_on_disk(ws, fake):
    (ws.paths.manifests / "shared").mkdir(parents=True)
    staging = ws.state_dir / "seed" / "reports"
    assert not staging.exists()

    code, out = run_cli(ws.root, "env", "up", "--dry-run")

    assert code == 0
    assert not staging.exists(), (
        f"dop env up --dry-run wrote a real placeholder under {staging} "
        f"(_seed_reports_dir() ignores ctx.dry_run entirely)"
    )


def test_env_up_dry_run_control_real_run_does_seed_it(ws, fake):
    (ws.paths.manifests / "shared").mkdir(parents=True)
    code, _ = run_cli(ws.root, "env", "up")
    assert code == 0
    assert (ws.state_dir / "seed" / "reports" / "index.html").exists()


# =================================================================================================
# B14 exit contract — gaps the existing suite's matrix does not reach: a bare (non-UsageError)
# exception from inside a verb's run(), and a KeyboardInterrupt raised while a verb is running.
# Neither path is exercised anywhere in tests/test_cli.py today.
# =================================================================================================

def test_cli_maps_a_bare_exception_from_run_to_exit_1_not_2(root, fake, monkeypatch):
    """A defect *inside dop* (not a usage/config problem) must not be reported as exit 2 — B14
    reserves 2 for "usage or configuration error, nothing attempted". cli.py's own comment agrees
    ("a defect in dop itself: still one exit point") and maps it to EXIT_FAILED. Pinning that."""
    import dop.verbs.status as status_mod

    def _boom(ctx):
        raise RuntimeError("boom from inside a verb")

    monkeypatch.setattr(status_mod, "run", _boom)
    code, _ = run_cli(root, "status")
    assert code == 1


def test_cli_maps_keyboardinterrupt_to_exit_1(root, fake, monkeypatch, capsys):
    """Ctrl-C mid-run is neither "usage error, nothing attempted" (2) nor cleanly "all done" (0);
    cli.py folds it into EXIT_FAILED (1), the same bucket as a unit that failed. Documented here
    because nothing in business-rules.md states this and nothing in the suite pins it — if the
    architect ever wants 130 (shell SIGINT convention) or a fifth code, this is the test that
    would need to move, and it should be an explicit decision, not a silent side effect of a
    refactor of the except chain in cli.main()."""
    import dop.verbs.status as status_mod

    def _interrupt(ctx):
        raise KeyboardInterrupt

    monkeypatch.setattr(status_mod, "run", _interrupt)
    code, _ = run_cli(root, "status")
    assert code == 1


# =================================================================================================
# Item 5 of the brief: --help / no verb / `dop test` without a sub-verb.
# =================================================================================================

def test_no_verb_exits_2_with_usage(root, fake):
    code, out = run_cli(root)
    assert code == 2
    assert "usage" in out.lower()


def test_test_verb_without_subverb_exits_2(root, fake):
    code, _ = run_cli(root, "test")
    assert code == 2


def test_test_verb_without_subverb_prints_top_level_help_not_test_help(root, fake):
    """Not a B-rule violation (business-rules.md says nothing about help text) — flagged to the
    lead as a weakness, not a defect: `dop test` (missing aaa|it|e2e) exits 2 correctly, but the
    help it prints is the *top-level* `dop --help` (cli.py always calls the outer `parser`, never
    the matched group's own subparser), so the user is told about `up`/`down`/... instead of being
    told to pick aaa, it or e2e."""
    code, out = run_cli(root, "test")
    assert code == 2
    assert "aaa" not in out and "e2e" not in out  # the one piece of help that would actually help
    assert "VERB" in out  # what they got instead: the top-level command list


# =================================================================================================
# Item 4 of the brief: --dry-run / --workspace placement, including on a nested verb.
# Both are argparse-shape questions, not business rules — pinned as current, safe (exit 2, not a
# silent bypass) behaviour, and reported as a weakness (surprising asymmetry) below.
# =================================================================================================

@pytest.mark.parametrize("where", ["before-top", "after-leaf"])
def test_dry_run_works_before_top_verb_and_after_leaf_verb(root, fake, where):
    fake.present()
    argv = ["--dry-run", "status"] if where == "before-top" else ["status", "--dry-run"]
    code, out = run_cli(root, *argv)
    assert code == 3  # nothing present
    assert "dry-run: nothing was changed" in out


def test_dry_run_between_a_verb_group_and_its_leaf_is_a_usage_error(root, fake):
    """`dop test --dry-run e2e` is NOT equivalent to `dop test e2e --dry-run` or
    `dop --dry-run test e2e`: the intermediate "test" group parser (built in cli.build_parser()'s
    words[:-1] loop) never gets _add_globals() called on it, so it has no --dry-run/--workspace of
    its own. Placed there, --dry-run is "unrecognized arguments" — a usage error (exit 2, safe),
    not a silent no-op. Still worth the lead knowing: `dop up --dry-run` and
    `dop test --dry-run e2e` look like the same idiom and only one of them works."""
    code, out = run_cli(root, "test", "--dry-run", "e2e")
    assert code == 2


def test_workspace_between_a_verb_group_and_its_leaf_is_a_usage_error(root, fake):
    code, out = run_cli(root, "test", "--workspace", str(root), "e2e")
    assert code == 2
