"""QA front F5 — what a demand's namespace contains (B2-B5, B9, B15, B18, B20, B30, B32, §6).

Oracle: docs/business-rules.md only. Reds here are left red on purpose: each names the rule it
holds the tool to. Sentinel secret values are fake (SENTINEL_*); no real env file is read.

Two groups:
  * synthetic — the conftest workspace + fakes on PATH (never a real cluster);
  * workspace — a COPY of /opt/wks/csptech/optum/{dop.toml,k8s} in tmp_path, rendered offline with
    the real `kubectl kustomize` (client-side, no cluster). Skipped when the workspace is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dop.config import load
from dop.outcome import UsageError
from dop.render import render_demand

from .conftest import CONFIG, REAL_KUBECTL, run_cli, worktree_dir

REAL_WS = Path("/opt/wks/csptech/optum")
SENTINELS = {
    "MONGODB_URI": "SENTINEL_MONGO_123",
    "AZURE_PROVIDER_ROOT_FOLDER": "SENTINEL_FOLDER_123",
    "EMAIL_PASSWORD": "SENTINEL_SECRET_123",
}

SYS_PY = shutil.which("python3")


class yaml:  # noqa: N801 — the venv has no PyYAML; the host python3 converts YAML to JSON
    @staticmethod
    def safe_load_all(text: str) -> list:
        proc = subprocess.run(
            [SYS_PY or "python3", "-c",
             "import sys,json,yaml; print(json.dumps([d for d in yaml.safe_load_all(sys.stdin) if d]))"],
            input=text, capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip(f"no YAML reader available: {proc.stderr.strip()[-80:]}")
        return json.loads(proc.stdout)

    @classmethod
    def safe_load(cls, text: str):
        return cls.safe_load_all(text)[0]


# ---------------------------------------------------------------------------------------------
# helpers


def _seed(ws, *apps):
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        (ws.paths.manifests / "demand" / kind_dir[ws.apps[name].kind] / name).mkdir(parents=True, exist_ok=True)


def _files_under(d: Path) -> set[str]:
    return {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()} if d.exists() else set()


@pytest.fixture
def real(tmp_path) -> Path:
    """A scratch copy of the real workspace's config and manifests, CRLF sentinel env file."""
    if not (REAL_WS / "dop.toml").is_file() or not (REAL_WS / "k8s").is_dir():
        pytest.skip("real workspace not present")
    w = tmp_path / "realws"
    (w / "docker").mkdir(parents=True)
    shutil.copy(REAL_WS / "dop.toml", w / "dop.toml")
    shutil.copytree(REAL_WS / "k8s", w / "k8s")
    (w / "docker" / ".env").write_bytes("".join(f"{k}={v}\r\n" for k, v in SENTINELS.items()).encode())
    return w


def _kustomize(overlay: Path) -> list[dict]:
    if REAL_KUBECTL is None:
        pytest.skip("kubectl not installed")
    proc = subprocess.run([REAL_KUBECTL, "kustomize", str(overlay)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def _one(docs, kind, name):
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} found"
    return found[0]


# ---------------------------------------------------------------------------------------------
# B15 — dry-run changes nothing (and so writes no secret to disk)


def test_up_dry_run_writes_nothing_under_the_workspace_state_dir(ws, root, fake):
    """B15: `--dry-run` "changes nothing". `up --dry-run` renders the overlay — including
    secret.env with the env file's VALUES — into <workspace>/.dop/overlays/<DEMAND>/, then prints
    "dry-run: nothing was changed". Observed live: SUOPT-3422/3460/3496 overlays with secret.env
    sit in /opt/wks/csptech/optum/.dop/overlays after dry-runs."""
    fake.present()
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    _seed(ws, "api")
    before = _files_under(ws.state_dir)
    code, out = run_cli(root, "up", "--tasks", "K-1", "--dry-run")
    assert code == 0 and "planned" in out
    written = _files_under(ws.state_dir) - before
    assert written == set(), f"dry-run wrote {sorted(written)}"


def test_env_up_dry_run_writes_nothing_under_the_workspace_state_dir(ws, fake):
    """B15, same shape: `env up --dry-run` writes .dop/seed/reports/index.html
    (env_up._seed_reports_dir runs before the dry-run check)."""
    (ws.paths.manifests / "shared").mkdir(parents=True)
    before = _files_under(ws.state_dir)
    code, _ = run_cli(ws.root, "env", "up", "--dry-run")
    assert code == 0
    written = _files_under(ws.state_dir) - before
    assert written == set(), f"dry-run wrote {sorted(written)}"


# ---------------------------------------------------------------------------------------------
# B30 — secret values never printed, never on a command line


def test_secret_values_never_printed_nor_on_any_argv(ws, root, fake):
    """B30 through every verb that touches the demand's config: up (dry and real), status, down.
    The fakes log every kubectl/docker argv, which is what `ps` would show."""
    fake.present()
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "feature/K-1"))
    _seed(ws, "api", "be", "fe")
    outs = []
    for argv in (("up", "--tasks", "K-1", "--dry-run"), ("up", "--tasks", "K-1")):
        outs.append(run_cli(root, *argv)[1])
    fake.present("K-1")
    for argv in (("status",), ("down", "--tasks", "K-1", "--dry-run"), ("down", "--tasks", "K-1")):
        outs.append(run_cli(root, *argv)[1])
    blob = "\n".join(outs) + json.dumps(fake.log("kubectl")) + json.dumps(fake.log("docker"))
    for secret in ("s3cret-db", "s3cret-mail"):
        assert secret not in blob
    overlay = ws.state_dir / "overlays" / "K-1"
    # amended v4 (B39): the secret file is transient — gone once the (last) real apply is over,
    # success or failure — so by now there is nothing left to check a mode on; its absence is
    # itself the assertion. architect 28/09
    assert not (overlay / "secret.env").exists(), "secret.env must not outlive the apply (B39)"
    for f in ("kustomization.yaml", "namespace.yaml"):
        assert "s3cret" not in (overlay / f).read_text()


# ---------------------------------------------------------------------------------------------
# B18 + B9 + B12 — `--app` narrows the units, never the demand's wiring


def test_up_with_app_filter_keeps_the_rest_of_the_demand_wired(ws, root, fake):
    """B12: `--app` narrows what the command acts on. B18: a callee *in the demand* is wired to its
    in-namespace Service. `up --tasks K-1 --app be` on a demand whose worktree also holds `api`
    re-renders the WHOLE namespace config from the narrowed list: API_URL flips to the remote
    fallback although `api` is still in the demand (and running), and the Secret loses api's keys
    — `kubectl apply` then deletes them from the live objects."""
    fake.present()
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    _seed(ws, "api", "be", "fe")
    assert run_cli(root, "up", "--tasks", "K-1")[0] == 0
    fake.present("K-1")
    code, out = run_cli(root, "up", "--tasks", "K-1", "--app", "be")
    assert code == 0, out
    k = json.loads((ws.state_dir / "overlays" / "K-1" / "kustomization.yaml").read_text())
    urls = [json.loads(p["patch"])["data"] for p in k["patches"]
            if p["target"] == {"kind": "ConfigMap", "name": "optum-urls"}]
    assert urls and urls[0]["API_URL"] == "http://api:8082", f"optum-urls after --app be: {urls}"
    assert any("backends/api" in r for r in k["resources"]), "api's workload dropped from the overlay"


# ---------------------------------------------------------------------------------------------
# B20 / B32 — schedulers


def test_status_never_claims_scheduler_off(ws, root, fake):
    """B32: "scheduler: on (not controllable)" unless every scheduler is covered; partial control
    is never shown as off. The conftest `api` DOES declare scheduler_off — still shown as on."""
    fake.present("K-1")
    fake.worktrees(root / "repos/api", (worktree_dir(root, "api", "K-1"), "K-1"))
    fake.worktrees(root / "repos/be", (worktree_dir(root, "be", "K-1"), "K-1"))
    code, out = run_cli(root, "status", "--tasks", "K-1")
    lines = [l for l in out.splitlines() if l.startswith("K-1/")]
    assert len(lines) == 3, out  # api, be, fe (companion)
    assert all("scheduler: on (not controllable)" in l for l in lines), out
    assert "off" not in out.replace("not controllable", "")


# ---------------------------------------------------------------------------------------------
# §6 — config strictness at every nesting level


def _load_with(root: Path, text: str):
    (root / "dop.toml").write_text(text)
    return load(root)


@pytest.mark.parametrize("mutate, where", [
    (lambda t: t.replace('fallback = "https://api.remote.example" }', 'fallback = "https://api.remote.example", x = 1 }'),
     r"calls\[0\]: unknown key"),
    (lambda t: t.replace('build = { image = "maven:3", command = ["mvn", "package"] }',
                         'build = { image = "maven:3", command = ["mvn", "package"], cache = "y" }'), "build: unknown key"),
    (lambda t: t.replace('service = "be"', 'service = "Be"'), "service"),
    (lambda t: t.replace('calls = [{ app = "api"', 'calls = [{ app = "nope"'), "unknown app 'nope'"),
    (lambda t: t.replace('companions = ["fe"]', 'companions = ["nope"]'), "unknown app 'nope'"),
    (lambda t: t.replace('BASE_URL = "{url:fe}"', 'BASE_URL = "{url:nope}"'), "unknown app"),
    (lambda t: t.replace('VITE_API = "{url:be}" }', 'VITE_API = "{url:nope}" }'), "unknown app"),
    (lambda t: t.replace('[apps.solo]', '[apps.Solo]'), "lower-case"),
    (lambda t: t.replace('port = 8095\n', ''), "missing required key"),
    (lambda t: t.replace('service = "solo"', 'service = "be"'), "duplicated service"),
])
def test_config_errors_are_exit_2(root, mutate, where):
    with pytest.raises(UsageError, match=where):
        _load_with(root, mutate(CONFIG))


def test_same_calls_key_to_different_apps_is_a_config_error(root, fake):
    """§6 "a duplicated name is a configuration error". Every back-end's `calls` keys land in ONE
    ConfigMap per namespace (render.py, `urls[c.key] = ...`), exactly like scheduler_off — which the
    loader DOES refuse on conflict. Two back-ends naming the same key for different callees is
    accepted, and render silently keeps whichever back-end comes last."""
    text = CONFIG.replace('secrets = ["DB_PASSWORD"]\nscheduler_off',
                          'secrets = ["DB_PASSWORD"]\ncalls = [{ app = "solo", key = "API_URL", '
                          'fallback = "https://solo.remote" }]\nscheduler_off')
    with pytest.raises(UsageError, match="API_URL"):
        _load_with(root, text)


# ---------------------------------------------------------------------------------------------
# the real workspace, rendered offline (item 5 of the brief, B2-B5, B9, B18, B30)


def test_real_workspace_every_app_matches_its_base(real):
    """The "render patch targets, by name" contract, per app: Deployment/Service/Ingress named
    after the app, Service port = dop.toml port, one Ingress rule, no tls, volume `artifact`
    hostPath Directory, and back-ends read optum-urls/optum-env/optum-app-secrets."""
    ws = load(real)
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name, app in ws.apps.items():
        d = ws.paths.manifests / "demand" / kind_dir[app.kind] / name
        assert d.is_dir(), f"{name}: no base dir {d}"
        docs = [x for f in sorted(d.glob("*.yaml")) if f.name != "kustomization.yaml"
                for x in yaml.safe_load_all(f.read_text()) if x]
        dep, svc, ing = _one(docs, "Deployment", name), _one(docs, "Service", app.service), _one(docs, "Ingress", name)
        assert [p["port"] for p in svc["spec"]["ports"] if p.get("name") == "http"] == [app.port], name
        assert svc["spec"].get("type", "ClusterIP") == "ClusterIP", name
        assert len(ing["spec"]["rules"]) == 1 and not ing["spec"].get("tls"), name
        rule = ing["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
        assert rule == {"name": app.service, "port": {"number": app.port}}, name
        vols = {v["name"]: v for v in dep["spec"]["template"]["spec"]["volumes"]}
        assert vols["artifact"]["hostPath"]["type"] == "Directory", name
        if app.kind == "backend":
            refs = {list(e.values())[0]["name"] for e in dep["spec"]["template"]["spec"]["containers"][0]["envFrom"]}
            assert {"optum-urls", "optum-env", "optum-app-secrets"} <= refs, name


def test_real_workspace_every_calls_key_is_in_the_base_configmap(real):
    ws = load(real)
    cm = yaml.safe_load((ws.paths.manifests / "demand/config/configmap-urls.yaml").read_text())
    keys = {c.key for a in ws.apps.values() if a.kind == "backend" for c in a.calls}
    assert keys <= set(cm["data"]), keys - set(cm["data"])


def test_real_workspace_full_render_is_clean(real):
    """All nine: one namespace labelled for dop, hosts `<app>.<demand>.localhost`, hostPath under
    /workspace/<ns>/<app> of type Directory, no NodePort/LoadBalancer/hostPort/hostNetwork, no
    base Secret, no database/queue image, Secret generated from the env file by config's keys."""
    ws = load(real)
    docs = _kustomize(render_demand(ws, "SUOPT-9001", list(ws.apps)))
    ns = "optum-suopt-9001"
    nss = [d for d in docs if d["kind"] == "Namespace"]
    assert [n["metadata"]["name"] for n in nss] == [ns]
    assert nss[0]["metadata"]["labels"] == {"app.kubernetes.io/managed-by": "dop", "dop/demand": "SUOPT-9001"}
    assert all(d["metadata"].get("namespace") == ns for d in docs if d["kind"] != "Namespace")
    assert sorted(d["metadata"]["name"] for d in docs if d["kind"] == "Deployment") == sorted(ws.apps)
    for d in docs:
        if d["kind"] == "Service":
            assert d["spec"].get("type", "ClusterIP") == "ClusterIP"
        if d["kind"] == "Ingress":
            assert [r["host"] for r in d["spec"]["rules"]] == [f"{d['metadata']['name']}.suopt-9001.localhost"]
        if d["kind"] == "Deployment":
            pod = d["spec"]["template"]["spec"]
            assert not pod.get("hostNetwork")
            for c in pod["containers"]:
                assert all("hostPort" not in p for p in c.get("ports", []))
                assert not any(w in c["image"] for w in ("mysql", "mongo", "postgres", "redis", "rabbit", "kafka"))
            hp = [v["hostPath"] for v in pod["volumes"] if "hostPath" in v]
            assert hp == [{"path": f"/workspace/{ns}/{d['metadata']['name']}", "type": "Directory"}]
    secrets = [d for d in docs if d["kind"] == "Secret"]
    assert [s["metadata"]["name"] for s in secrets] == ["optum-app-secrets"]
    assert sorted(secrets[0]["data"]) == sorted(SENTINELS)  # CRLF env file: no \r in keys


def test_real_workspace_render_holds_only_the_demands_apps(real):
    """B9: the base kustomization lists all nine; the overlay must bring only the demand's."""
    ws = load(real)
    docs = _kustomize(render_demand(ws, "SUOPT-9002", ["optum-support-be", "optum-support-fe"]))
    for kind in ("Deployment", "Service", "Ingress"):
        assert sorted(d["metadata"]["name"] for d in docs if d["kind"] == kind) == ["optum-support-be", "optum-support-fe"]
    sec = _one(docs, "Secret", "optum-app-secrets")
    assert sorted(sec["data"]) == ["AZURE_PROVIDER_ROOT_FOLDER", "EMAIL_PASSWORD"]  # no MONGODB_URI


def test_real_workspace_network_policy_isolates_the_namespace(real):
    """B4 (static half): ingress denied by default to every pod; the one opening admits only the
    ingress controller's pods from kube-system, on the named `http` port (JDWP stays closed)."""
    ws = load(real)
    docs = _kustomize(render_demand(ws, "SUOPT-9003", ["lifesupport-api"]))
    pols = {d["metadata"]["name"]: d["spec"] for d in docs if d["kind"] == "NetworkPolicy"}
    deny = [p for p in pols.values() if p["podSelector"] == {} and "ingress" not in p]
    assert deny and deny[0]["policyTypes"] == ["Ingress"]
    for name, p in pols.items():
        for rule in p.get("ingress", []):
            assert rule.get("from"), f"{name}: rule with no `from` admits everyone"
            for peer in rule["from"]:
                assert peer.get("namespaceSelector") == {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}
                assert peer.get("podSelector") == {"matchLabels": {"app.kubernetes.io/name": "traefik"}}
            assert [pt["port"] for pt in rule["ports"]] == ["http"]


def _effective_urls(ws, demand, apps) -> dict[str, str]:
    docs = _kustomize(render_demand(ws, demand, apps))
    return _one(docs, "ConfigMap", "optum-urls")["data"]


@pytest.mark.parametrize("apps", [
    ["lifesupport-api", "optum-support-be", "optum-support-fe"],   # k8s/overlays/demand-example
    ["lifesupport-api", "providers-back-end", "providers-front-end"],
])
def test_real_workspace_partial_demand_env_points_every_lifesupport_key_in_namespace(real, apps):
    """B18: "the in-namespace Service when the callee is in the demand". Every back-end mounts the
    WHOLE optum-urls ConfigMap (envFrom). The base ships LIFE_SUPPORTE_API_URL (two Ps) = Azure;
    render patches it only when canal-empresa-be/appoptum-be is in the demand. Spring Boot 2.5.7
    relaxed binding maps LIFE_SUPPORTE_API_URL straight onto `life-supporte.api-url` — the property
    optum-support-be's and providers-back-end's Feign clients read — and an env var outranks the
    `${LIFESUPPORT_API_URL:...}` placeholder in application-desenv.yml. So in this demand the BFF
    calls Azure's lifesupport, not the one running beside it. (Binding proven with the 2.5.7
    Binder from ~/.m2; see the QA report.)"""
    ws = load(real)
    urls = _effective_urls(ws, "SUOPT-9004", apps)
    in_ns = "http://lifesupport-api:8082"
    lifesupport_keys = {k: v for k, v in urls.items() if "LIFE" in k}
    assert all(v == in_ns for v in lifesupport_keys.values()), lifesupport_keys
