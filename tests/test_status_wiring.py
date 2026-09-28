"""dop status --wiring: per-demand wiring, visible and verifiable.

Fakes only (tests/fakes); no cluster is touched. `be` calls `api` (in the fixture workspace's
[apps.be].calls, key API_URL) and `fe` calls `be` (key VITE_API) — see tests/conftest.CONFIG.
"""

from __future__ import annotations

import json
import os

from .conftest import FAKES, run_cli, worktree_dir


def _configmap_kubectl(tmp_path, monkeypatch, data: dict | None) -> None:
    """kubectl on PATH ahead of the real fake: answers `get configmap optum-urls` with `data`
    (None: not found, `--ignore-not-found` gives empty output); everything else falls through to
    the real fake (tests/fakes/kubectl)."""
    bindir = tmp_path / "kbin"
    bindir.mkdir(exist_ok=True)
    k = bindir / "kubectl"
    # The payload is written to a file and `cat`, avoiding shell quoting entirely.
    payload_file = bindir / "configmap.json"
    payload_file.write_text(json.dumps({"data": data}) if data is not None else "")
    script = (
        "#!/bin/sh\n"
        'case " $* " in\n'
        f'  *"configmap optum-urls"*) cat {payload_file}; exit 0;;\n'
        "esac\n"
        f'exec {FAKES / "kubectl"} "$@"\n'
    )
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
# any demand with `be` — accounted for below rather than fought.

def test_wiring_line_local_when_callee_is_in_the_demand(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "K-1/be: API_URL -> http://api:8082 [local api]" in out


def test_wiring_line_remote_when_callee_is_not_in_the_demand(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "be")  # api not in this demand
    _configmap_kubectl(tmp_path, monkeypatch,
                        data={"API_URL": "https://api.remote.example", "VITE_API": "http://be:8090"})
    code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
    assert "K-1/be: API_URL -> https://api.remote.example [remote api: not in the demand]" in out


def test_wiring_section_appears_under_normal_status_too(root, fake, ws, tmp_path, monkeypatch):
    k1 = _k1(root, fake, ws, "api", "be")
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"})
    code, out = run_cli(k1, "status", "--tasks", "K-1")
    assert code == 0, out
    assert "K-1/be: not deployed" in out  # the usual readiness line
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
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"})
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
    _configmap_kubectl(tmp_path, monkeypatch, data={"API_URL": "http://api:8082", "VITE_API": "http://be:8090"})
    with lock(ws.state_dir / "locks", Unit("app", "K-1", "be")):
        code, out = run_cli(k1, "status", "--tasks", "K-1", "--wiring")
    assert code == 0, out
