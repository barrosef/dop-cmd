"""dop status --wiring: per-demand wiring, visible and verifiable.

Fakes only (tests/fakes); no cluster is touched. `be` calls `api` (in the fixture workspace's
[apps.be].calls, key API_URL) and `fe` calls `be` (key VITE_API) — see tests/conftest.CONFIG.
"""

from __future__ import annotations

import json
import os

from .conftest import FAKES, run_cli, worktree_dir


_HEALTHY_DEPLOYMENT = {"spec": {"replicas": 1}, "status": {"readyReplicas": 1}}


def _configmap_kubectl(tmp_path, monkeypatch, data: dict | None, deployments: dict | None = None) -> None:
    """kubectl on PATH ahead of the real fake: answers `get configmap optum-urls` with `data`
    (None: not found, `--ignore-not-found` gives empty output).

    Answers `get deployment <name>` not-found by default, same as the real fake — a scenario that
    needs a callee actually running names it in `deployments` (a payload dict, e.g. 1/1 ready;
    `None` still means "no Deployment"). `deployments` is by app name.

    Everything else falls through to the real fake (tests/fakes/kubectl)."""
    bindir = tmp_path / "kbin"
    bindir.mkdir(exist_ok=True)
    k = bindir / "kubectl"
    configmap_file = bindir / "configmap.json"
    configmap_file.write_text(json.dumps({"data": data}) if data is not None else "")
    dep_dir = bindir / "deployments"
    dep_dir.mkdir(exist_ok=True)
    for name, payload in (deployments or {}).items():
        (dep_dir / f"{name}.json").write_text(json.dumps(payload) if payload is not None else "")
    script = f'''#!/usr/bin/env python3
import os, sys

argv = sys.argv[1:]

if "configmap" in argv and "optum-urls" in argv:
    p = "{configmap_file}"
    if os.path.exists(p) and os.path.getsize(p):
        sys.stdout.write(open(p).read())
    sys.exit(0)

if "deployment" in argv:
    name = argv[argv.index("deployment") + 1]
    p = os.path.join("{dep_dir}", name + ".json")
    if os.path.exists(p) and os.path.getsize(p):
        sys.stdout.write(open(p).read())
    sys.exit(0)

os.execv("{FAKES / "kubectl"}", ["{FAKES / "kubectl"}", *argv])
'''
    k.write_text(script)
    k.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _seed(ws, *apps):
    kind_dir = {"backend": "backends", "frontend": "frontends"}
    for name in apps:
        (ws.paths.manifests / "demand" / kind_dir[ws.apps[name].kind] / name).mkdir(parents=True, exist_ok=True)


def _k1(root, fake, ws, *apps):
    _seed(ws, *apps)
    fake.present("K-1")
    for a in apps:
        fake.worktrees(root / "repos" / a, (worktree_dir(root, a, "K-1"), "K-1"))
    return root


# `be`'s companion `fe` is pulled into every demand that has `be` (config.App.companions); it has
# calls of its own (VITE_API -> be), so both its wiring line and its own ConfigMap key are part of
# any demand with `be` — accounted for below rather than fought. Since B18 amended, `fe`'s wiring
# also checks `be` itself is running, so a demand exercising `be`'s calls has to fake both callees
# (`api` for `be`, `be` for `fe`) as up, or that second, unasked-for check fails the whole run.

def test_wiring_line_local_when_callee_is_in_the_demand(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": _HEALTHY_DEPLOYMENT, "be": _HEALTHY_DEPLOYMENT})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "K-1/be: API_URL -> http://api:8082 [local api]" in out


def test_wiring_line_remote_when_callee_is_not_in_the_demand(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "be")  # api not in this demand
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "https://api.remote.example", "VITE_API": "http://be:8090"},
                        deployments={"be": _HEALTHY_DEPLOYMENT})  # fe's callee, in the demand
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "K-1/be: API_URL -> https://api.remote.example [remote api: not in the demand]" in out


def test_wiring_section_appears_under_normal_status_too(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": _HEALTHY_DEPLOYMENT, "be": _HEALTHY_DEPLOYMENT})
    code, out = run_cli(k1, "status", "--tasks", "K-1")
    assert code == 0, out
    assert "K-1/be: 1/1 ready" in out  # be's own readiness line, now that it is faked running
    assert "API_URL -> http://api:8082 [local api]" in out  # the wiring line, indented


def test_app_without_calls_has_no_wiring_line(root, fake, ws):
    k1 = _k1(root, fake, ws, "solo")
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "solo" not in out  # nothing printed for it


def test_dry_run_prints_expected_only_no_live_check(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://something-else:1"})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring", "--dry-run")
    assert code == 0, out  # would have drifted, but dry-run never checks
    assert "API_URL -> http://api:8082 [local api]" in out
    assert not [c for c in fake.log("kubectl") if "configmap" in c]


def test_missing_configmap_fails_the_unit(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data=None)
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 1
    assert "optum-urls" in out and "not found" in out and "dop up --tasks K-1" in out


def test_matching_configmap_is_silent(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": _HEALTHY_DEPLOYMENT, "be": _HEALTHY_DEPLOYMENT})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "drift" not in out


def test_drifted_key_fails_the_unit_with_the_expected_value(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://stale:9999", "VITE_API": "http://be:8090"})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 1
    assert "wiring drift: KEY API_URL, expected http://api:8082" in out
    assert "dop up --tasks K-1" in out


def test_status_still_takes_no_lock_with_wiring(root, fake, ws, tmp_path, monkeypatch):
    from dop.lock import lock
    from dop.outcome import Unit

    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": _HEALTHY_DEPLOYMENT, "be": _HEALTHY_DEPLOYMENT})
    with lock(ws.state_dir / "locks", Unit("app", "K-1", "be")):
        code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out


# -- B18 amended: a local callee's own liveness, not just its ConfigMap value --------------------

def test_callee_deployment_missing_marks_local_line_not_running_and_fails(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": None})  # no Deployment at all
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 1
    assert "K-1/be: API_URL -> http://api:8082 [local api — NOT RUNNING]" in out
    assert "local api is not running" in out and "dop up --tasks K-1 --app api" in out


def test_callee_deployment_zero_ready_marks_local_line_not_running_and_fails(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": {"spec": {"replicas": 1}, "status": {"readyReplicas": 0}}})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 1
    assert "[local api — NOT RUNNING]" in out


def test_callee_deployment_running_is_silent(root, fake, ws, tmp_path, monkeypatch):
    """Both `be`'s callee (`api`) and `fe`'s (`be` itself, its companion) faked running."""
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": _HEALTHY_DEPLOYMENT, "be": _HEALTHY_DEPLOYMENT})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "NOT RUNNING" not in out


def test_remote_callee_not_checked_for_liveness(root, fake, ws, tmp_path, monkeypatch):
    """`api` is not in this demand (remote, fallback address): its Deployment is never queried,
    however it is faked, because it is not this demand's to bring up. `be` is faked running for
    `fe`'s own local call to it (its companion, also in the demand)."""
    k1 = _k1(root, fake, ws, "be")  # api not in this demand
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "https://api.remote.example", "VITE_API": "http://be:8090"},
                        deployments={"api": None, "be": _HEALTHY_DEPLOYMENT})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "NOT RUNNING" not in out


def test_dry_run_still_skips_the_liveness_check(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"},
                        deployments={"api": None})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring", "--dry-run")
    assert code == 0, out
    assert "NOT RUNNING" not in out
