"""render_demand: the overlay patches the named targets (spec) — B3, B4, B6, B18, B19, B20, B26, B30."""

import json
import subprocess

import pytest

from dop.outcome import Unit
from dop.render import (RenderError, SECRET_FILE, apply_selector, check_rendered, plan_demand,
                        render_demand, rendered)

from .conftest import REAL_KUBECTL


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _kustomization(d, *resources):
    _write(d / "kustomization.yaml", {"apiVersion": "kustomize.config.k8s.io/v1beta1",
                                      "kind": "Kustomization", "resources": list(resources)})


def _base(root):
    """A minimal base shaped like the spec's patch targets."""
    b = root / "k8s" / "demand"
    _write(b / "config" / "cm.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "optum-urls"},
                                      "data": {"API_URL": "https://base.example"}})
    _write(b / "config" / "env.yaml", {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "optum-env"},
                                       "data": {"X": "1"}})
    _kustomization(b / "config", "cm.yaml", "env.yaml")
    _write(b / "frontends" / "spa-conf" / "cm.yaml", {"apiVersion": "v1", "kind": "ConfigMap",
                                                      "metadata": {"name": "spa-conf"}, "data": {}})
    _kustomization(b / "frontends" / "spa-conf", "cm.yaml")
    for kind, app in (("backends", "api"), ("backends", "be"), ("backends", "solo"), ("frontends", "fe")):
        d = b / kind / app
        _write(d / "workload.yaml", {
            "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": app},
            "spec": {"selector": {"matchLabels": {"app": app}}, "template": {
                "metadata": {"labels": {"app": app}},
                "spec": {"containers": [{"name": "c", "image": "busybox"}],
                         "volumes": [{"name": "artifact", "hostPath": {"path": "/placeholder", "type": "Directory"}}]}}}})
        _write(d / "ingress.yaml", {
            "apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {"name": app},
            "spec": {"rules": [{"host": f"demand.{app}.localhost"}]}})
        _kustomization(d, "workload.yaml", "ingress.yaml")


@pytest.fixture
def base(root):
    _base(root)
    return root


def _read(out):
    return json.loads((out / "kustomization.yaml").read_text())


def test_overlay_location_and_resources(ws, base):
    out = render_demand(ws, "K-1", ["be"])
    assert out == base / ".dop" / "overlays" / "K-1"
    k = _read(out)
    assert k["namespace"] == "optum-k-1"
    assert k["resources"] == ["namespace.yaml", "../../../k8s/demand/config", "backends/be"]
    ns = json.loads((out / "namespace.yaml").read_text())
    assert ns["metadata"]["labels"] == {"app.kubernetes.io/managed-by": "dop", "dop/demand": "K-1"}
    # each app's base comes through a wrapper labelling it, so `up --app` can narrow the apply (B37)
    wrapper = json.loads((out / "backends" / "be" / "kustomization.yaml").read_text())
    assert wrapper["resources"] == ["../../../../../k8s/demand/backends/be"]
    assert wrapper["labels"] == [{"pairs": {"dop/app": "be"}}]


def test_frontend_brings_shared_frontend_dirs(ws, base):
    k = _read(render_demand(ws, "K-1", ["fe"]))
    assert "../../../k8s/demand/frontends/spa-conf" in k["resources"]


def test_wiring_service_when_in_demand_else_fallback(ws, base):
    def urls(apps):
        k = _read(render_demand(ws, "K-1", apps))
        p = [x for x in k["patches"] if x["target"] == {"kind": "ConfigMap", "name": "optum-urls"}]
        return json.loads(p[0]["patch"])["data"]

    assert urls(["be", "api"]) == {"API_URL": "http://api:8082"}
    assert urls(["be"]) == {"API_URL": "https://api.remote.example"}
    # every key any back-end of the workspace declares, even with that back-end out of the
    # demand: the base holds no value dop resolves (B37, D16)
    assert urls(["api"]) == {"API_URL": "http://api:8082"}
    assert urls(["solo"]) == {"API_URL": "https://api.remote.example"}


def test_secret_comes_from_env_file_and_is_private(ws, base):
    out = render_demand(ws, "K-1", ["be", "api"])
    secret = out / "secret.env"
    assert secret.read_text() == "DB_PASSWORD=s3cret-db\nMAIL_PASSWORD=s3cret-mail\n"
    assert secret.stat().st_mode & 0o777 == 0o600
    gen = _read(out)["secretGenerator"][0]
    assert gen["name"] == "optum-app-secrets" and gen["options"]["disableNameSuffixHash"] is True
    assert "s3cret" not in (out / "kustomization.yaml").read_text()


def test_missing_secret_key_fails(ws, base, root):
    (root / "docker" / ".env").write_text("OTHER=1\n")
    with pytest.raises(RenderError, match="DB_PASSWORD"):
        render_demand(ws, "K-1", ["api"])


def test_missing_app_base_fails(ws, base, root):
    import shutil
    shutil.rmtree(root / "k8s" / "demand" / "backends" / "solo")
    with pytest.raises(RenderError, match="solo"):
        render_demand(ws, "K-1", ["solo"])


@pytest.mark.skipif(REAL_KUBECTL is None, reason="kubectl not installed")
def test_kustomize_renders_the_patch_targets(ws, base):
    units = [Unit("app", "K-1", "be", source="worktree"),
             Unit("app", "K-1", "api", source="worktree"),
             Unit("app", "K-1", "fe", source="trunk", ref="main@abc1234 dirty")]
    out = render_demand(ws, "K-1", units)
    proc = subprocess.run([REAL_KUBECTL, "kustomize", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout
    assert "fe.k-1.localhost" in text and "be.k-1.localhost" in text
    assert "/workspace/optum-k-1/fe" in text and "/placeholder" not in text
    assert "namespace: optum-k-1" in text
    assert "dop/source: trunk" in text and "main@abc1234 dirty" in text
    assert "SCHEDULING_ENABLED: \"false\"" in text
    assert "http://api:8082" in text
    assert "optum-app-secrets" in text
    assert "demand.fe.localhost" not in text


# -- B38 / B39: dry-run writes nothing; the secret file is transient ------------------------------

def test_plan_writes_nothing(ws, base):
    plan = plan_demand(ws, "K-1", ["be", "api"])
    assert not (base / ".dop").exists()
    assert "kustomization.yaml" in plan.files and "s3cret" not in json.dumps(plan.files)
    assert plan.secret == "DB_PASSWORD=s3cret-db\nMAIL_PASSWORD=s3cret-mail\n"


def test_plan_fails_what_render_fails(ws, base, root):
    (root / "docker" / ".env").write_text("OTHER=1\n")
    with pytest.raises(RenderError, match="DB_PASSWORD"):
        plan_demand(ws, "K-1", ["api"])
    assert not (base / ".dop").exists()


@pytest.mark.parametrize("boom", [False, True])
def test_rendered_removes_the_secret_file_success_or_failure(ws, base, boom):
    seen = {}
    try:
        with rendered(ws, "K-1", ["be"]) as out:
            seen["secret"] = (out / SECRET_FILE).read_text()
            if boom:
                raise RuntimeError("apply failed")
    except RuntimeError:
        assert boom
    assert seen["secret"].startswith("DB_PASSWORD=")
    assert not (out / SECRET_FILE).exists()
    assert (out / "kustomization.yaml").exists()


# -- B37: cluster-scoped kinds refused; patch targets must match -----------------------------------

@pytest.mark.parametrize("text, bad", [
    ("kind: Namespace\n---\nkind: ConfigMap\n", None),
    ("kind: Namespace\n---\nkind: ClusterRoleBinding\n", "ClusterRoleBinding"),
    ("kind: Namespace\n---\nkind: Namespace\n", "Namespace"),
    ("kind: Namespace\nsubjects:\n- kind: ClusterRole\n", None),  # nested, not a document's kind
])
def test_check_rendered_refuses_cluster_scoped_kinds(text, bad):
    if bad is None:
        check_rendered(text)
    else:
        with pytest.raises(RenderError, match=bad):
            check_rendered(text)


def test_apply_selector_narrows_only_workloads():
    assert apply_selector(["be", "api", "fe"], ["be", "api", "fe"]) == []
    assert apply_selector(["be", "api", "fe"], ["be"]) == ["-l", "dop/app notin (api,fe)"]


@pytest.mark.skipif(REAL_KUBECTL is None, reason="kubectl not installed")
@pytest.mark.parametrize("remove", ["ingress.yaml", "cm"])
def test_kustomize_fails_when_a_patch_target_is_missing(ws, base, root, remove):
    d = root / "k8s" / "demand"
    if remove == "cm":
        _write(d / "config" / "cm.yaml", {"apiVersion": "v1", "kind": "ConfigMap",
                                          "metadata": {"name": "renamed"}, "data": {}})
    else:
        (d / "backends" / "be" / remove).unlink()
        _kustomization(d / "backends" / "be", "workload.yaml")
    out = render_demand(ws, "K-1", ["be"])
    proc = subprocess.run([REAL_KUBECTL, "kustomize", str(out)], capture_output=True, text=True)
    assert proc.returncode != 0


@pytest.mark.skipif(REAL_KUBECTL is None, reason="kubectl not installed")
def test_kustomize_output_labels_each_app_for_the_selector(ws, base):
    out = render_demand(ws, "K-1", ["be", "api"])
    proc = subprocess.run([REAL_KUBECTL, "kustomize", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "dop/app: be" in proc.stdout and "dop/app: api" in proc.stdout
    check_rendered(proc.stdout)


def test_b18_call_in_browser_form_gets_the_public_address():
    """A calls entry marked form="browser" (e.g. a link base put into e-mails) resolves to the
    demand's public hostname, not the in-namespace service address."""
    from dop.config import Call
    assert Call("fe", "APP_FRONT_URL", "https://x", "browser").form == "browser"
    assert Call("fe", "K", "https://x").form == "service"
