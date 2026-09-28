"""Shared fixtures: a workspace in tmp_path and fake kubectl/docker/git first on PATH."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from dop import cli
from dop.config import load
from dop.context import Context, Filters

FAKES = Path(__file__).parent / "fakes"
REAL_KUBECTL = shutil.which("kubectl")

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

[repos.api]
dir = "repos/api"
[repos.be]
dir = "repos/be"
[repos.fe]
dir = "repos/fe"
[repos.solo]
dir = "repos/solo"

[apps.api]
repo = "api"
kind = "backend"
service = "api"
port = 8082
artifact = "target"
build = { image = "maven:3", command = "mvn package" }
secrets = ["DB_PASSWORD"]
scheduler_off = { SCHEDULING_ENABLED = "false" }

[apps.be]
repo = "be"
kind = "backend"
service = "be"
port = 8090
artifact = "target"
build = { image = "maven:3", command = ["mvn", "package"] }
calls = [{ app = "api", key = "API_URL", fallback = "https://api.remote.example" }]
companions = ["fe"]
secrets = ["DB_PASSWORD", "MAIL_PASSWORD"]

[apps.fe]
repo = "fe"
kind = "frontend"
service = "fe"
port = 8080
artifact = "dist"
build = { image = "node:22", command = "npm run build", env = { VITE_API = "{url:be}" } }
calls = [{ app = "be", key = "VITE_API", fallback = "https://be.remote.example" }]
forbidden = ["localhost:8090"]
companions = ["be"]
suite = "fe"
suite_env = { BASE_URL = "{url:fe}" }

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


class Fake:
    """Handle on the fakes' state."""

    def __init__(self, state: Path):
        self.state = state
        (state / "git").mkdir(parents=True)

    def contexts(self, *names: str) -> None:
        (self.state / "contexts").write_text("\n".join(names) + "\n")

    def namespaces(self, *items: dict) -> None:
        (self.state / "namespaces.json").write_text(json.dumps(list(items)))

    def present(self, *keys: str) -> None:
        self.namespaces(*[
            {"name": f"optum-{k.lower()}", "labels": {"app.kubernetes.io/managed-by": "dop", "dop/demand": k}}
            for k in keys
        ])

    def kube_fail(self) -> None:
        (self.state / "kube_fail").write_text("1")

    def worktrees(self, repo_dir: Path, *entries: tuple[Path, str | None]) -> None:
        """Main checkout on main, then (path, branch) entries."""
        blocks = [f"worktree {repo_dir}\nHEAD {'a' * 40}\nbranch refs/heads/main\n"]
        for path, branch in entries:
            b = f"worktree {path}\nHEAD {'b' * 40}\n"
            b += f"branch refs/heads/{branch}\n" if branch else "detached\n"
            blocks.append(b)
        (self.state / "git" / f"{Path(repo_dir).name}.porcelain").write_text("\n".join(blocks))

    def git_fail(self, repo_dir: Path) -> None:
        (self.state / "git" / f"{Path(repo_dir).name}.fail").write_text("1")

    def log(self, tool: str) -> list[list[str]]:
        p = self.state / f"{tool}.log"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture
def fake(tmp_path, monkeypatch) -> Fake:
    state = tmp_path / "fake-state"
    f = Fake(state)
    f.contexts("k3d-test", "client-prod")
    monkeypatch.setenv("DOP_FAKE_STATE", str(state))
    monkeypatch.setenv("PATH", f"{FAKES}:{__import__('os').environ['PATH']}")
    return f


@pytest.fixture
def root(tmp_path) -> Path:
    ws = tmp_path / "ws"
    (ws / "docker").mkdir(parents=True)
    (ws / "docker" / ".env").write_text("DB_PASSWORD=s3cret-db\nMAIL_PASSWORD='s3cret-mail'\nOTHER=x\n")
    for r in ("api", "be", "fe", "solo"):
        (ws / "repos" / r).mkdir(parents=True)
    (ws / "dop.toml").write_text(CONFIG)
    return ws


@pytest.fixture
def ws(root):
    return load(root)


def make_ctx(ws, *, tasks=(), apps=(), repos=(), dry_run=False) -> Context:
    return Context(ws=ws, filters=Filters(tuple(tasks), tuple(apps), tuple(repos)),
                   dry_run=dry_run, out=io.StringIO())


def run_cli(root: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(["--workspace", str(root), *argv], out=out)
    return code, out.getvalue()


def worktree_dir(root: Path, repo: str, demand: str) -> Path:
    d = root / ".worktrees" / repo / demand.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d
