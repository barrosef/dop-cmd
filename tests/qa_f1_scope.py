"""QA front F1 — which demand, which code, which units (B7, B8, B9, B11, B12, B19, B26, B28, B31).

Reds here are product defects or weaknesses reported to the QA lead; they stay red until the product
changes. Each test names the rule it holds the tool to.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from dop.config import load
from dop.context import demand_key
from dop.outcome import UsageError
from dop.render import plan_demand, render_demand
from dop.scope import branch_matches, resolve
from dop.verbs import registry

from .conftest import FAKES, make_ctx, run_cli, worktree_dir

VERBS = {name: mod.VERB for name, mod in registry().items()}


# ---------------------------------------------------------------------------------------------
# B7 — a demand key is ^[A-Za-z]+-\d+$, upper-cased


@pytest.mark.parametrize("raw", [
    "SUOPT-1530\n",            # Python `$` matches before a trailing newline
    "SUOPT-١٥٣٠",  # Arabic-Indic digits: Python `\d` is Unicode
    "SUOPT-１５３０",  # full-width digits
])
def test_b7_refuses_keys_the_ascii_rule_does_not_allow(raw):
    """B7 is an ASCII key rule; the key becomes a namespace and a DNS label (B3, B4), where none of
    these can live. They must be a usage error (exit 2), not a demand."""
    with pytest.raises(UsageError):
        demand_key(raw)


def test_b7_trailing_newline_key_reaches_the_summary(root, fake):
    """Evidence for the above end to end: exit 3 with the newline printed inside the unit label,
    instead of exit 2."""
    code, out = run_cli(root, "--dry-run", "status", "--tasks", "SUOPT-1530\n")
    assert code == 2, out


def test_b7_key_that_can_never_be_a_namespace_is_a_usage_error(root, fake):
    """WEAKNESS (B4/B14): namespace `optum-<key>` and label value `dop/demand=<KEY>` are limited
    to 63 characters. A 60-character key passes B7, is planned by `up --dry-run` (exit 0) and can
    only fail at `kubectl apply`. Nothing should be attempted: exit 2."""
    key = "A" * 57 + "-1"  # 59 chars: label value OK, namespace optum-... is 65 > 63
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", key), f"feature/{key}"))
    (root / "k8s/demand/backends/be").mkdir(parents=True)
    (root / "k8s/demand/frontends/fe").mkdir(parents=True)
    code, out = run_cli(root, "--dry-run", "up", "--tasks", key)
    assert code == 2, out


def test_b7_same_key_in_three_cases_is_one_demand(root, fake):
    fake.present("K-1")
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "--dry-run", "status", "--tasks", "k-1", "K-1", "K-1")
    assert out.count("K-1/solo") == 1, out


# ---------------------------------------------------------------------------------------------
# B28 — token matching


@pytest.mark.parametrize("key, branch, hit", [
    ("SUOPT-346", "bug/SUOPT-3460", False),        # prefix of another key
    ("SUOPT-3460", "bug/SUOPT-346", False),
    ("SUOPT-3460", "bug/SUOPT-3460+x", False),     # + is not a delimiter
    ("SUOPT-3460", "bug/SUOPT-3460@x", False),
    ("SUOPT-3460", "bug#SUOPT-3460", False),
    ("SUOPT-3460", "feat/SUOPT-3460-contexto", True),
    ("SUOPT-3460", "SUOPT-3460/SUOPT-3460", True),
    ("SUOPT-3460", "teste/3460-em-dev", False),    # number alone is not the key
])
def test_b28_token_edges(key, branch, hit):
    assert branch_matches(key, branch) is hit


# ---------------------------------------------------------------------------------------------
# Real git (B8, B28): only the fake kubectl/docker on PATH, real `git`


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "q", "GIT_AUTHOR_EMAIL": "q@q",
                        "GIT_COMMITTER_NAME": "q", "GIT_COMMITTER_EMAIL": "q@q"})


@pytest.fixture
def realgit(tmp_path, monkeypatch):
    """fake kubectl + docker, real git. Returns the fake state dir."""
    state = tmp_path / "fake-state"
    (state / "git").mkdir(parents=True)
    (state / "contexts").write_text("k3d-test\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("kubectl", "docker"):
        (bindir / tool).symlink_to(FAKES / tool)
    monkeypatch.setenv("DOP_FAKE_STATE", str(state))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return state


def _init_repos(root: Path, names=("api", "be", "fe", "solo")):
    for r in names:
        d = root / "repos" / r
        if d.exists():
            for p in d.iterdir():
                raise AssertionError(f"unexpected {p}")
            d.rmdir()
        _git("init", "-q", "-b", "main", str(d))
        _git("-C", str(d), "commit", "-q", "--allow-empty", "-m", "init")


def test_b28_worktree_path_gone_fails_naming_it(root, realgit):
    _init_repos(root)
    wt = root / "wt" / "solo-7"
    _git("-C", str(root / "repos/solo"), "worktree", "add", "-q", "-b", "bug/K-7", str(wt))
    subprocess.run(["rm", "-rf", str(wt)], check=True)
    scope = resolve(make_ctx(load(root), tasks=["K-7"]), VERBS["up"])
    reasons = [r.reason for r in scope.skipped if r.unit.name == "solo"]
    assert reasons and str(wt) in reasons[0] and "no longer exists" in reasons[0]
    assert not scope.units


def test_b8_repo_dir_that_is_not_a_repo_is_not_the_workspace_repo(root, realgit):
    """DEFECT (B8, B31). The workspace is itself a git repository (it is: /opt/wks/csptech/optum)
    and holds its own worktree for the demand (B22 expects one). A configured repository whose
    directory exists but is not a git repository (clone not done yet, clone abandoned) makes
    `git -C repos/solo worktree list` walk up to the WORKSPACE repo, so app `solo` resolves to the
    workspace's worktree and would be built and deployed from it. Expected: that repository's
    worktrees cannot be read — the unit fails (or the repo is not part of the demand) — never the
    workspace repo's worktree."""
    _init_repos(root, ("api", "be", "fe"))  # repos/solo stays a plain directory
    (root / ".gitignore").write_text("repos/\nwt/\n.dop/\n")
    _git("init", "-q", "-b", "master", str(root))
    _git("-C", str(root), "add", ".gitignore", "dop.toml")
    _git("-C", str(root), "commit", "-q", "-m", "ws")
    ws_wt = root / "wt" / "ws-k1"
    _git("-C", str(root), "worktree", "add", "-q", "-b", "test/K-1", str(ws_wt))

    scope = resolve(make_ctx(load(root), tasks=["K-1"]), VERBS["up"])
    solo = [u for u in scope.units if u.name == "solo"]
    assert not solo, f"solo resolved to {solo[0].path} ({solo[0].source}) — the workspace repo"


def test_b31_companion_label_names_another_demands_branch(root, realgit):
    # amended v4 (B8): the main checkout is never a demand's worktree, whatever its branch — it is
    # only ever the companion source (B31), labelled with its real branch. K-9 itself has no
    # worktree anywhere (be's main checkout on feat/K-9 does not count), architect 28/09
    """Real-workspace shape: repos/optum-support-be main checkout is ON feat/SUOPT-3419, so a
    demand that needs it as a companion gets another demand's code. B31 requires the label to say
    so — it does (this is green; the sharing itself is reported to the lead as a rule gap)."""
    _init_repos(root)
    _git("-C", str(root / "repos/be"), "checkout", "-q", "-b", "feat/K-9")  # main checkout on K-9
    _git("-C", str(root / "repos/fe"), "worktree", "add", "-q", "-b", "bug/K-2", str(root / "wt/fe-k2"))
    ws = load(root)
    k9 = resolve(make_ctx(ws, tasks=["K-9"]), VERBS["up"])
    k2 = resolve(make_ctx(ws, tasks=["K-2"]), VERBS["up"])
    assert not any(u.name == "be" for u in k9.units), (
        "be's main checkout on feat/K-9 must not count as K-9's own worktree (B8 amended)"
    )
    assert any(r.reason and "no application" in r.reason for r in k9.skipped), k9.skipped
    be2 = next(u for u in k2.units if u.name == "be")
    assert be2.source == "trunk" and be2.path == root / "repos/be"
    assert be2.ref.startswith("feat/K-9@")


# ---------------------------------------------------------------------------------------------
# B12 — filters only narrow; `up --app` must not rewrite the rest of the demand


def _overlay(root: Path, demand: str):
    # amended v4 (B38/B39): dry-run writes nothing anywhere, so these read the overlay a REAL `up`
    # left behind. `kustomization.yaml` persists (render.write); `secret.env` does not — it is
    # transient (B39), gone by the time the CLI returns. Callers that need the secret keys go
    # through `render.plan_demand()` instead, architect 28/09
    out = root / ".dop" / "overlays" / demand
    k = json.loads((out / "kustomization.yaml").read_text())
    cms = {json.loads(p["patch"])["metadata"]["name"]: json.loads(p["patch"])["data"]
           for p in k["patches"] if p["target"]["kind"] == "ConfigMap"}
    return cms


@pytest.fixture
def k1_full(root, fake):
    """K-1 has worktrees for api and be (fe joins as companion); base manifests present."""
    for d in ("config", "backends/api", "backends/be", "backends/solo", "frontends/fe"):
        (root / "k8s/demand" / d).mkdir(parents=True)
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "fix/K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    return root


def test_b12_up_without_app_wires_be_to_the_demands_api(k1_full, fake):
    # amended v4 (B38): --dry-run writes nothing (nowhere to inspect on disk); a REAL `up` is run
    # instead, and the ConfigMap wiring is read from the overlay it leaves behind, architect 28/09
    code, out = run_cli(k1_full, "up", "--tasks", "K-1")
    assert code == 0, out
    cms = _overlay(k1_full, "K-1")
    assert cms["optum-urls"]["API_URL"] == "http://api:8082"
    assert cms["optum-env"] == {"SCHEDULING_ENABLED": "false"}


def test_b12_up_app_narrowing_keeps_be_wired_to_the_demands_api(k1_full, fake):
    """DEFECT (B12, B18). `up --tasks K-1 --app be` renders the demand's optum-urls from the
    FILTERED units only: api is still in the demand (and still running in the namespace), but
    be's API_URL is rewritten to the remote fallback. Applied, it points the demand's be at the
    remote api on its next restart. Narrowing chose which apps to act on; it changed the wiring."""
    # amended v4 (B38): real run, not --dry-run — see test above, architect 28/09
    code, out = run_cli(k1_full, "up", "--tasks", "K-1", "--app", "be")
    assert code == 0, out
    cms = _overlay(k1_full, "K-1")
    assert cms["optum-urls"]["API_URL"] == "http://api:8082"


def test_b12_up_app_narrowing_keeps_schedulers_off(k1_full, fake):
    """DEFECT (B12, B20). The same narrowing drops api's scheduler_off from optum-env; applied,
    the base optum-env comes back and api's schedulers are on after its next restart."""
    # amended v4 (B38): real run, not --dry-run — see first test in this group, architect 28/09
    code, out = run_cli(k1_full, "up", "--tasks", "K-1", "--app", "be")
    assert code == 0, out
    cms = _overlay(k1_full, "K-1")
    assert cms.get("optum-env") == {"SCHEDULING_ENABLED": "false"}


def test_b12_up_app_narrowing_keeps_the_demands_secret(k1_full, fake):
    """DEFECT (B12, B30). `up --app fe` (a front-end, no secrets) renders an EMPTY secret.env;
    applied, the demand's Secret optum-app-secrets loses every key its back-ends read."""
    # amended v4 (B38/B39): --dry-run leaves nothing to inspect; a real run's secret.env is gone
    # by the time the CLI returns (transient, B39). What must hold is B37: render is always the
    # WHOLE demand regardless of --app, so plan_demand() over the demand's full app set — the same
    # computation `up` ran internally — must still carry every back-end secret key; and neither the
    # key names nor the values may ever have reached kubectl's argv or dop's own stdout (B30).
    # architect 28/09
    ws = load(k1_full)
    scope = resolve(make_ctx(ws, tasks=["K-1"], apps=["fe"]), VERBS["up"])
    every_app = scope.demand_apps["K-1"]
    code, out = run_cli(k1_full, "up", "--tasks", "K-1", "--app", "fe")
    assert code == 0, out

    plan = plan_demand(ws, "K-1", every_app)
    assert {line.split("=", 1)[0] for line in plan.secret.splitlines() if line} == {
        "DB_PASSWORD", "MAIL_PASSWORD",
    }

    secret_file = k1_full / ".dop" / "overlays" / "K-1" / "secret.env"
    assert not secret_file.exists(), "secret.env must not remain after a real apply (B39)"
    assert "s3cret-db" not in out and "s3cret-mail" not in out
    for call in fake.log("kubectl"):
        joined = " ".join(call)
        assert "s3cret-db" not in joined and "s3cret-mail" not in joined
        assert "DB_PASSWORD=" not in joined and "MAIL_PASSWORD=" not in joined


def test_b12_app_outside_the_demand_is_not_silent(k1_full, fake):
    """WEAKNESS (B12/B13 silence). `--app solo` for a demand without solo gives `summary: 0 units`
    and exit 3 with no line saying why; the operator cannot tell a typo-free but wrong filter from
    an empty demand."""
    code, out = run_cli(k1_full, "--dry-run", "up", "--tasks", "K-1", "--app", "solo")
    assert code == 3
    assert "solo" in out, out


def test_b12_app_narrowing_never_widens(k1_full, fake):
    scope = resolve(make_ctx(load(k1_full), tasks=["K-1"], apps=["fe", "solo"]), VERBS["up"])
    assert [(u.name, u.source) for u in scope.units] == [("fe", "trunk")]


def test_b12_unknown_app_and_foreign_filter_are_exit_2(root, fake):
    assert run_cli(root, "status", "--app", "nope")[0] == 2
    assert run_cli(root, "status", "--repo", "be")[0] == 2
    assert run_cli(root, "down", "--tasks", "K-1", "--app", "be")[0] == 2
    assert run_cli(root, "test", "aaa", "--repo", "nope")[0] == 2


# ---------------------------------------------------------------------------------------------
# B11 / B26 — discovery by label only, and the label must be the namespace's


def test_b11_nothing_present_says_so_exit_3(root, fake):
    fake.namespaces()
    code, out = run_cli(root, "--dry-run", "status")
    assert code == 3 and "nothing present" in out


def test_b11_malformed_and_lower_case_labels(root, fake):
    m = {"app.kubernetes.io/managed-by": "dop"}
    fake.namespaces(
        {"name": "optum-k-1", "labels": {**m, "dop/demand": "k-1"}},
        {"name": "optum-garbage", "labels": {**m, "dop/demand": "garbage"}},
        {"name": "optum-nolabel", "labels": m},
    )
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    scope = resolve(make_ctx(load(root)), VERBS["status"])
    assert [(u.demand, u.name) for u in scope.units] == [("K-1", "solo")]


def test_b26_down_never_deletes_an_unlabelled_namespace_because_another_carries_the_label(root, fake):
    """DEFECT (B26). optum-k-9 belongs to someone else (no dop labels). Another namespace, `decoy`,
    carries managed-by=dop and dop/demand=K-9. `present_demands` trusts the label to say K-9 is
    present, then `down` recomputes the name optum-k-9 and deletes THAT namespace. B26: `down`
    acts only on namespaces dop labelled; the one it deletes is not. Control: without the decoy
    the same command fails the unit and touches nothing."""
    m = {"app.kubernetes.io/managed-by": "dop"}
    fake.namespaces(
        {"name": "optum-k-9", "labels": {"owner": "someone-else"}},
        {"name": "decoy", "labels": {**m, "dop/demand": "K-9"}},
    )
    code, out = run_cli(root, "down", "--tasks", "K-9")
    deletes = [a for a in fake.log("kubectl") if "delete" in a]
    assert deletes == [], f"deleted {deletes}; exit {code}\n{out}"


def test_b26_control_unlabelled_namespace_alone_is_not_touched(root, fake):
    fake.namespaces({"name": "optum-k-9", "labels": {"owner": "someone-else"}})
    code, out = run_cli(root, "down", "--tasks", "K-9")
    assert code == 1 and "not labelled by dop" in out
    assert not [a for a in fake.log("kubectl") if "delete" in a]


def test_b26_status_after_prefix_change_does_not_report_a_ghost(root, fake):
    """DEFECT (B26 / status). A namespace dop labelled K-1 is `old-k-1` (namespace_prefix was
    changed in config since). Discovery lists K-1 as present; every verb then addresses optum-k-1,
    which does not exist: status reports K-1's apps "not deployed" (done) while the real namespace
    sits unmanaged, and `down` would report done having deleted nothing. The label and the name
    disagree; the unit should fail naming it."""
    m = {"app.kubernetes.io/managed-by": "dop"}
    fake.namespaces({"name": "old-k-1", "labels": {**m, "dop/demand": "K-1"}})
    fake.worktrees(root / "repos/solo", (worktree_dir(root, "solo", "K-1"), "K-1"))
    code, out = run_cli(root, "down", "--tasks", "K-1")
    assert code == 1, out


# ---------------------------------------------------------------------------------------------
# B19 — demands with no app, and companions that are themselves worktree apps


def test_b19_demand_only_in_repos_without_apps_is_skipped_no_namespace(root, fake):
    fake.namespaces()
    code, out = run_cli(root, "--dry-run", "up", "--tasks", "K-5")
    assert code == 3 and "no application" in out
    assert not [a for a in fake.log("kubectl") if "apply" in a]
    assert not (root / ".dop" / "overlays" / "K-5").exists()


def test_b19_failed_worktree_app_is_not_readded_as_trunk_companion(root, fake):
    """be has two candidate worktrees (B8 fails the unit). Its companion fe is not built from
    trunk under be's name, and be itself does not come back as fe's companion from trunk."""
    fake.worktrees(root / "repos/be",
                   (worktree_dir(root, "be", "K-1"), "K-1"), (root / "x", "feature/K-1-b"))
    fake.worktrees(root / "repos/fe", (worktree_dir(root, "fe", "K-1"), "K-1"))
    scope = resolve(make_ctx(load(root), tasks=["K-1"]), VERBS["up"])
    assert [(u.name, u.source) for u in scope.units] == [("fe", "worktree")]
    assert [(r.unit.name, r.status.value) for r in scope.skipped] == [("be", "failed")]
