"""The per-demand kustomize overlay (spec: "Render patch targets, by name").

render_demand() writes `<workspace>/.dop/overlays/<DEMAND>/` and returns it; `kubectl kustomize`
(or `apply -k`) on that directory gives everything the demand's namespace holds. Files are JSON,
which is YAML, so no YAML library is needed.

Base layout the overlay points at, under `paths.manifests`:
    demand/<dir>/              every directory except backends/ and frontends/ (config, policy, ...)
    demand/backends/<app>/     one kustomization per back-end app
    demand/frontends/<app>/    one kustomization per front-end app
    demand/{backends,frontends}/<other>/   a directory not named after a configured app is shared
                               by that kind (e.g. an nginx conf), included when the demand has an
                               app of that kind

Each app's base is included through a small wrapper in the overlay (`<kind dir>/<app>/`) that labels
everything in it `dop/app=<app>`, so `up --app` can apply only some workloads (`apply_selector`)
while the config it applies is always the whole demand's (B37).

Patch targets, by name: Namespace (generated, labels B26); Ingress `<app>` host (B3); Deployment
`<app>` volume `artifact` hostPath (B6) and source labels (B19, B31); ConfigMap `optum-urls` (B18);
ConfigMap `optum-env` (B20); Secret `optum-app-secrets` generated here (B30). A named target that
does not exist fails the render (B37): each is also the source of a no-op replacement, which
kustomize refuses when nothing is selected.

plan_demand() computes all of it and writes nothing (B38: dry-run). The secret file is transient
(B39): `rendered()` removes it when the apply is over, success or failure.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .address import address, host, namespace
from .config import Workspace
from .outcome import Unit
from .scope import DEMAND_LABEL, MANAGED_BY, MANAGED_BY_VALUE, SOURCE_TRUNK, SOURCE_WORKTREE

URLS_CONFIGMAP = "optum-urls"
ENV_CONFIGMAP = "optum-env"
SECRET = "optum-app-secrets"
SECRET_FILE = "secret.env"
ARTIFACT_VOLUME = "artifact"
SOURCE_LABEL = "dop/source"
SOURCE_REF_ANNOTATION = "dop/source-ref"
APP_LABEL = "dop/app"

_KIND_DIR = {"backend": "backends", "frontend": "frontends"}

# Kinds that live outside any namespace. A demand's base may hold none of them (B37): applying one
# from a demand would reach every other demand. The overlay's own Namespace is the one exception.
CLUSTER_SCOPED_KINDS = frozenset({
    "Namespace", "Node", "PersistentVolume", "StorageClass", "VolumeAttachment", "CSIDriver",
    "CSINode", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition", "APIService",
    "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration",
    "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding", "PriorityClass",
    "RuntimeClass", "IngressClass", "PodSecurityPolicy", "CertificateSigningRequest",
    "FlowSchema", "PriorityLevelConfiguration", "VolumeSnapshotClass", "ClusterIssuer",
})
_TOP_KIND = re.compile(r"^kind:\s*[\"']?([A-Za-z0-9]+)[\"']?\s*$", re.MULTILINE)


class RenderError(Exception):
    """The overlay cannot be rendered (a base directory or a secret value is missing, a cluster-
    scoped kind in the base)."""


@dataclass
class Plan:
    """What the overlay holds. `files` are relative to the overlay directory and hold no secret;
    `secret` is the env file content of the Secret — never printed."""

    demand: str
    out: Path
    files: dict[str, object] = field(default_factory=dict)
    secret: str = ""


def _rel(target: Path, start: Path) -> str:
    return os.path.relpath(target, start)


def _guard(kind: str, name: str) -> dict:
    """A no-op replacement (the name onto itself) that makes kustomize fail when `kind/name` is
    not in the base: a patch target that does not match fails the demand (B37)."""
    return {
        "source": {"kind": kind, "name": name, "fieldPath": "metadata.name"},
        "targets": [{"select": {"kind": kind, "name": name}, "fieldPaths": ["metadata.name"]}],
    }


def overlay_dir(ws: Workspace, demand: str) -> Path:
    return ws.state_dir / "overlays" / demand


def plan_demand(ws: Workspace, demand: str, apps: Iterable[Unit | str]) -> Plan:
    """Everything the overlay holds, computed, nothing written.

    apps: every app of the demand (B9 + B19) — never a narrowed list (B37) — as (demand, app)
    units, whose `source`/`ref` label the workloads, or plain app names (taken as worktree apps).
    """
    units = [a if isinstance(a, Unit) else Unit("app", demand, a, source=SOURCE_WORKTREE) for a in apps]
    names = [u.name for u in units]
    unknown = [n for n in names if n not in ws.apps]
    if unknown:
        raise RenderError(f"unknown app(s): {', '.join(unknown)}")

    ns = namespace(ws, demand)
    base = ws.paths.manifests / "demand"
    out = overlay_dir(ws, demand)
    plan = Plan(demand, out)

    # -- resources
    resources = ["namespace.yaml"]
    if not base.is_dir():
        raise RenderError(f"{base}: base manifests not found")
    app_dirs = set(ws.apps)
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        if d.name not in _KIND_DIR.values():
            resources.append(_rel(d, out))
    kinds = {ws.apps[n].kind for n in names}
    for kind in sorted(kinds):
        kdir = base / _KIND_DIR[kind]
        for d in sorted(p for p in kdir.iterdir() if p.is_dir()) if kdir.is_dir() else []:
            if d.name not in app_dirs:
                resources.append(_rel(d, out))
    for n in names:
        d = base / _KIND_DIR[ws.apps[n].kind] / n
        if not d.is_dir():
            raise RenderError(f"{d}: no base manifests for app {n}")
        wrapper = f"{_KIND_DIR[ws.apps[n].kind]}/{n}"
        plan.files[f"{wrapper}/kustomization.yaml"] = {
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "resources": [_rel(d, out / wrapper)],
            "labels": [{"pairs": {APP_LABEL: n}}],
        }
        resources.append(wrapper)

    # -- namespace (B4, B26)
    plan.files["namespace.yaml"] = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": ns, "labels": {MANAGED_BY: MANAGED_BY_VALUE, DEMAND_LABEL: demand}},
    }

    patches = []
    guards = []

    # -- per app: Ingress host (B3); Deployment artifact hostPath (B6) and source (B19, B31)
    for u in units:
        patches.append({
            "target": {"kind": "Ingress", "name": u.name},
            "patch": json.dumps([{"op": "replace", "path": "/spec/rules/0/host", "value": host(ws, demand, u.name)}]),
        })
        source = u.source or SOURCE_WORKTREE
        metadata = {"name": u.name, "labels": {SOURCE_LABEL: source}}
        if source == SOURCE_TRUNK and u.ref:
            metadata["annotations"] = {SOURCE_REF_ANNOTATION: u.ref}
        patches.append({
            "target": {"kind": "Deployment", "name": u.name},
            "patch": json.dumps({
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": metadata,
                "spec": {"template": {"spec": {"volumes": [{
                    "name": ARTIFACT_VOLUME,
                    "hostPath": {"path": f"{ws.cluster.node_root}/{ns}/{u.name}", "type": "Directory"},
                }]}}},
            }),
        })
        guards += [_guard("Ingress", u.name), _guard("Deployment", u.name)]

    # -- back-end wiring (B18, B37): every key any back-end of the workspace declares, resolved for
    # this demand — the in-namespace Service when the callee is in the demand, else its fallback.
    # The base holds no value dop resolves; a key left to it would be one nobody decided.
    urls: dict[str, str] = {}
    for app in ws.apps.values():
        if app.kind != "backend":
            continue
        for c in app.calls:
            urls[c.key] = address(ws, demand, c.app, c.form) if c.app in names else c.fallback
    if urls:
        patches.append({
            "target": {"kind": "ConfigMap", "name": URLS_CONFIGMAP},
            "patch": json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                                 "metadata": {"name": URLS_CONFIGMAP}, "data": urls}),
        })
        guards.append(_guard("ConfigMap", URLS_CONFIGMAP))

    # -- schedulers off (B20)
    sched: dict[str, str] = {}
    for n in names:
        sched.update(ws.apps[n].scheduler_off)
    if sched:
        patches.append({
            "target": {"kind": "ConfigMap", "name": ENV_CONFIGMAP},
            "patch": json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                                 "metadata": {"name": ENV_CONFIGMAP}, "data": sched}),
        })
        guards.append(_guard("ConfigMap", ENV_CONFIGMAP))

    # -- the Secret, from the env file by the keys config names (B30); always present
    keys = list(dict.fromkeys(k for n in names for k in ws.apps[n].secrets))
    env = ws.env() if keys else {}
    missing = [k for k in keys if k not in env]
    if missing:
        raise RenderError(f"{ws.paths.env_file}: missing secret key(s): {', '.join(missing)}")
    plan.secret = "".join(f"{k}={env[k]}\n" for k in keys)

    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": ns,
        "resources": resources,
        "secretGenerator": [{
            "name": SECRET,
            "envs": [SECRET_FILE],
            "options": {"disableNameSuffixHash": True},
        }],
        "patches": patches,
    }
    if guards:
        kustomization["replacements"] = guards
    plan.files["kustomization.yaml"] = kustomization
    return plan


def write(plan: Plan) -> Path:
    """Write the plan's overlay, the secret file included (0600). The directory is rewritten
    whole."""
    out = plan.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for rel, obj in plan.files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    fd = os.open(out / SECRET_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(plan.secret)
    return out


def render_demand(ws: Workspace, demand: str, apps: Iterable[Unit | str]) -> Path:
    """Write the demand's overlay and return its directory, the secret file included — the caller
    owns removing it (`rendered()` does, B39). Secret values are never printed."""
    return write(plan_demand(ws, demand, apps))


@contextmanager
def rendered(ws: Workspace, demand: str, apps: Iterable[Unit | str]) -> Iterator[Path]:
    """The written overlay for the duration of the block; the secret file is deleted afterwards,
    success or failure (B39)."""
    plan = plan_demand(ws, demand, apps)
    try:
        yield write(plan)
    finally:
        (plan.out / SECRET_FILE).unlink(missing_ok=True)


def check_rendered(text: str) -> None:
    """Refuse cluster-scoped kinds in the rendered demand (B37); the one Namespace is the overlay's
    own. `text` is `kubectl kustomize` output, whose documents carry `kind:` at column 0."""
    kinds = _TOP_KIND.findall(text)
    bad = sorted({k for k in kinds if k in CLUSTER_SCOPED_KINDS and k != "Namespace"})
    if kinds.count("Namespace") > 1:
        bad.insert(0, "Namespace")
    if bad:
        raise RenderError(f"cluster-scoped kind(s) in the demand base: {', '.join(bad)}; refused (B37)")


def apply_selector(all_apps: Iterable[str], chosen: Iterable[str]) -> list[str]:
    """`kubectl apply` arguments that apply only the `chosen` apps' workloads, and everything that
    is not an app's (namespace, config, secret, policy) — `--app` narrows workloads only (B37)."""
    chosen = set(chosen)
    left_out = [a for a in dict.fromkeys(all_apps) if a not in chosen]
    return ["-l", f"{APP_LABEL} notin ({','.join(left_out)})"] if left_out else []
