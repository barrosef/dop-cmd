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

Patch targets, by name: Namespace (generated, labels B26); Ingress `<app>` host (B3); Deployment
`<app>` volume `artifact` hostPath (B6) and source labels (B19, B31); ConfigMap `optum-urls` (B18);
ConfigMap `optum-env` (B20); Secret `optum-app-secrets` generated here (B30).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from .address import address, host, namespace
from .config import Workspace
from .outcome import Unit
from .scope import DEMAND_LABEL, MANAGED_BY, MANAGED_BY_VALUE, SOURCE_TRUNK, SOURCE_WORKTREE

URLS_CONFIGMAP = "optum-urls"
ENV_CONFIGMAP = "optum-env"
SECRET = "optum-app-secrets"
ARTIFACT_VOLUME = "artifact"
SOURCE_LABEL = "dop/source"
SOURCE_REF_ANNOTATION = "dop/source-ref"

_KIND_DIR = {"backend": "backends", "frontend": "frontends"}


class RenderError(Exception):
    """The overlay cannot be rendered (a base directory or a secret value is missing)."""


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _rel(target: Path, start: Path) -> str:
    return os.path.relpath(target, start)


def render_demand(ws: Workspace, demand: str, apps: Iterable[Unit | str]) -> Path:
    """Write the demand's overlay and return its directory.

    apps: the demand's apps (B9 + B19), as (demand, app) units — their `source`/`ref` label the
    workloads — or plain app names (taken as worktree apps). The directory is rewritten whole on
    every call. Secret values are written to a 0600 file inside it and never printed.
    """
    units = [a if isinstance(a, Unit) else Unit("app", demand, a, source=SOURCE_WORKTREE) for a in apps]
    names = [u.name for u in units]
    unknown = [n for n in names if n not in ws.apps]
    if unknown:
        raise RenderError(f"unknown app(s): {', '.join(unknown)}")

    ns = namespace(ws, demand)
    base = ws.paths.manifests / "demand"
    out = ws.state_dir / "overlays" / demand
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

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
        resources.append(_rel(d, out))

    # -- namespace (B4, B26)
    _dump(out / "namespace.yaml", {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": ns, "labels": {MANAGED_BY: MANAGED_BY_VALUE, DEMAND_LABEL: demand}},
    })

    patches = []

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

    # -- back-end wiring (B18): the in-namespace Service when the callee is in the demand
    urls: dict[str, str] = {}
    for n in names:
        app = ws.apps[n]
        if app.kind != "backend":
            continue
        for c in app.calls:
            urls[c.key] = address(ws, demand, c.app, "service") if c.app in names else c.fallback
    if urls:
        patches.append({
            "target": {"kind": "ConfigMap", "name": URLS_CONFIGMAP},
            "patch": json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                                 "metadata": {"name": URLS_CONFIGMAP}, "data": urls}),
        })

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

    # -- the Secret, from the env file by the keys config names (B30); always present
    keys = list(dict.fromkeys(k for n in names for k in ws.apps[n].secrets))
    env = ws.env() if keys else {}
    missing = [k for k in keys if k not in env]
    if missing:
        raise RenderError(f"{ws.paths.env_file}: missing secret key(s): {', '.join(missing)}")
    secret_file = out / "secret.env"
    fd = os.open(secret_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for k in keys:
            fh.write(f"{k}={env[k]}\n")

    _dump(out / "kustomization.yaml", {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": ns,
        "resources": resources,
        "secretGenerator": [{
            "name": SECRET,
            "envs": ["secret.env"],
            "options": {"disableNameSuffixHash": True},
        }],
        "patches": patches,
    })
    return out
