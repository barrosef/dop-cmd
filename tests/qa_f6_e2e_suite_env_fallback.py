"""QA F6 — `test e2e`'s suite_env rendering for an app outside the demand (B17 parity, B21).

Oracle: docs/business-rules.md B17 (build.env: a referenced app's address in the demand, or its
declared fallback when the app is outside it), B21 ("every address a suite reads comes from config
templates rendered with the scheme -- no default in any suite may be relied on"), B33.
Real-world shape this reproduces (read-only inspection, not touched):
/opt/wks/csptech/optum/dop.toml `[apps.providers-front-end]` declares
`suite_env = { ..., E2E_SUOPT3137_APPOPTUM_URL = "{url:appoptum-be}", E2E_SUPORTE_BFF_URL =
"{url:optum-support-be}" }` while its only `companions` is `providers-back-end` and its only
`calls` fallback is for `lifesupport-api` -- appoptum-be and optum-support-be have no fallback
declared, and are not implied by providers-front-end's own worktree/companion set (B9, B19).
Scope: FRONT (test report verbs) — src/dop/verbs/_testing.py (run_suite_unit), address.py (expand).
Uses a self-contained scratch workspace (tmp_path via the `root`-style fixture below), not the
shared `dop.toml`/`test/e2e/reports` of the real workspace.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dop.config import load
from dop.outcome import Status, Unit
from dop.verbs._testing import run_suite_unit

CONFIG = """\
[cluster]
context = "k3d-test"
node = "k3d-test-server-0"
node_root = "/workspace"

[address]
domain = "localhost"
port = 8080
namespace_prefix = "optum"
shared_namespace = "optum-shared"

[paths]
manifests = "k8s"
env_file = "docker/.env"
reports = "test/e2e/reports"
test_root = "test"

[repos.fe]
dir = "repos/fe"
[repos.be]
dir = "repos/be"
[repos.solo]
dir = "repos/solo"

[apps.be]
repo = "be"
kind = "backend"
service = "be"
port = 8090
artifact = "target"
build = { image = "maven:3", command = "mvn package" }
companions = ["fe"]

[apps.fe]
repo = "fe"
kind = "frontend"
service = "fe"
port = 8080
artifact = "dist"
build = { image = "node:22", command = "npm run build" }
companions = ["be"]
suite = "fe"
suite_env = { BASE_URL = "{url:fe}", API_URL = "{url:be}", FOREIGN_URL = "{url:solo}" }

[apps.solo]
repo = "solo"
kind = "backend"
service = "solo"
port = 8095
artifact = "target"
build = { image = "maven:3", command = "mvn package" }

[runners]
maven = "maven:3"
e2e = "playwright:1"
allure = "allure:2"
"""


@pytest.fixture
def scratch_root(tmp_path) -> Path:
    ws = tmp_path / "ws"
    (ws / "docker").mkdir(parents=True)
    (ws / "docker" / ".env").write_text("OTHER=x\n")
    for r in ("fe", "be", "solo"):
        (ws / "repos" / r).mkdir(parents=True)
    (ws / "dop.toml").write_text(CONFIG)
    suite_dir = ws / "test" / "e2e" / "fe"
    suite_dir.mkdir(parents=True)
    (suite_dir / "test_x.py").write_text(
        "import os\n"
        "BASE_URL = os.environ.get('BASE_URL')\n"
        "API_URL = os.environ.get('API_URL')\n"
        "FOREIGN_URL = os.environ.get('FOREIGN_URL')\n"
    )
    return ws


class _RecordingRunner:
    def __init__(self):
        self.calls = []

    def run(self, image, mounts=(), env=None, network=None, workdir=None, command=(), **kw):
        self.calls.append({"image": image, "env": dict(env or {})})
        return SimpleNamespace(returncode=0)


class _NoOpNode:
    def sync(self, *a, **k):
        pass


def test_suite_env_renders_a_real_address_for_an_app_outside_the_demand(scratch_root, fake):
    """`fe`'s only companion is `be` (B19): K-1's demand apps are exactly {fe, be} (B9). `solo` is
    a real, configured app that is neither fe's worktree app, nor its companion, nor covered by
    any `calls` fallback of fe's -- yet fe's `suite_env` reads `{url:solo}` (exactly the shape of
    the real providers-front-end -> appoptum-be / optum-support-be references, see module
    docstring).
    """
    fake.present("K-1")
    ws = load(scratch_root)

    ctx = SimpleNamespace(ws=ws, dry_run=False, options={}, runner=_RecordingRunner(), node=_NoOpNode())
    unit = Unit("suite", "K-1", "fe", app="fe")

    result = run_suite_unit(ctx, unit)
    env = ctx.runner.calls[0]["env"] if ctx.runner.calls else None

    # Expected (B17 parity): verbs/build.py's build_env() resolves `apps=` (the demand's actual
    # apps, via scope.demand_apps) and `fallbacks=app.fallbacks()` before calling expand(), so a
    # front-end build.env reference to an app outside the demand with no declared fallback raises
    # UsageError and fails the unit cleanly, naming the app. run_suite_unit calls
    # `expand(tmpl, ws, demand)` with neither `apps=` nor `fallbacks=`, so expand() takes its
    # `apps=None` branch ("every referenced app is taken to be in the demand") unconditionally --
    # `solo` gets rendered as if it were deployed in K-1's namespace.
    assert result.status is Status.FAILED, (
        "'solo' is outside K-1's demand apps ({fe, be} via B9/B19) and fe declares no `calls` "
        f"fallback for it, but run_suite_unit did not refuse it: status={result.status}, "
        f"reason={result.reason!r}, rendered env={env!r}. Unlike build.py's build_env (B17), "
        "run_suite_unit never passes apps=/fallbacks= to address.expand(), so an app outside the "
        "demand is silently pointed at a per-demand address "
        "(http://solo.k-1.localhost:8080) that nothing deploys there -- the e2e suite will hit a "
        "hostname with no matching ingress in K-1's namespace instead of getting a named, "
        "actionable failure or a working fallback address."
    )
