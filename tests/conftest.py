"""Shared fixtures: a full dop.toml (the spec's example) written to tmp_path."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

EXAMPLE = textwrap.dedent(
    '''
    [project]
    name = "dop"
    state_dir = ".dop"
    default_environment = "local"

    [environments.local]
    dir = "repos/dop-infra"
    kubeconfig = "k3d kubeconfig write dop-local"
    namespace = "dop-local"
    cluster_exists = "k3d cluster list dop-local"
    cluster_running = "k3d cluster list dop-local -o json"
    cluster_up = "make -C repos/dop-infra cluster-up"
    cluster_start = "k3d cluster start dop-local"
    cluster_stop = "make -C repos/dop-infra cluster-stop"
    prepare = ["make -C repos/dop-infra images", "make -C repos/dop-infra image-runner"]
    up = "make -C repos/dop-infra up"
    down = "make -C repos/dop-infra down"
    status = "make -C repos/dop-infra status"
    workloads = ["sts/postgres", "sts/nats", "sts/firebase", "deploy/secretmanager"]
    wait_timeout = "240s"
    addresses = ["http://api.localtest.me:8080"]

    [components.app]
    repo = "repos/dop-app"
    kind = "process"
    needs = ["bff"]
    setup = "pnpm install --frozen-lockfile"
    run = "pnpm -C artifacts/dop dev"
    env = { PORT = "5173" }
    ready = "http://localhost:5173"
    address = "http://localhost:5173"
    [components.app.test]
    unit = ["pnpm typecheck", "pnpm -C artifacts/dop test"]

    [components.bff]
    repo = "repos/dop-api"
    kind = "cluster"
    needs = ["core"]
    workloads = ["deploy/dop-api"]
    logs = "make -C repos/dop-infra logs C=dop-api"
    ready = "http://api.localtest.me:8080/healthz"
    setup = "uv sync"
    [components.bff.test]
    unit = ["make test"]

    [components.core]
    repo = "repos/dop-core"
    kind = "cluster"
    workloads = ["deploy/dop-core-serve"]
    [components.core.test]
    unit = ["make test"]
    [components.core.test.integration]
    forwards = ["svc/postgres 5432:5432", "svc/nats 4222:4222"]
    commands = ["make test-integration"]
    [components.core.test.contract]
    forwards = ["svc/nats 4222:4222"]
    commands = ["make test-contract-integration", "make proto-breaking"]

    [components.cli]
    repo = "repos/dop-cli"
    kind = "none"
    [components.cli.test]
    unit = ["uv run python -m pytest -q"]
    '''
)


@pytest.fixture
def write_config(tmp_path: Path):
    def _write(text: str = EXAMPLE, name: str = "dop.toml") -> Path:
        path = tmp_path / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def example_config(write_config) -> Path:
    return write_config()
