"""The workspace: `<workspace>/dop.toml`, loaded strictly (§6, B30).

An unknown key, a missing required key, a wrong type, an application naming an unknown repository
or application, a duplicated name, or a missing env file is a configuration error: UsageError,
exit 2, nothing attempted. Nothing here names an application.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .outcome import UsageError

CONFIG_NAME = "dop.toml"

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URL_REF = re.compile(r"\{url:([^}]*)\}")

KINDS = ("backend", "frontend")


@dataclass(frozen=True)
class Cluster:
    context: str
    node: str
    node_root: str


@dataclass(frozen=True)
class AddressScheme:
    domain: str
    port: int
    namespace_prefix: str
    shared_namespace: str


@dataclass(frozen=True)
class Paths:
    """Absolute paths, resolved against the workspace root."""

    manifests: Path
    env_file: Path
    reports: Path
    test_root: Path


@dataclass(frozen=True)
class Repo:
    name: str
    dir: Path  # the main checkout, absolute


@dataclass(frozen=True)
class Build:
    image: str
    command: tuple[str, ...]  # a string in the file becomes ("sh", "-c", string)
    env: dict[str, str] = field(default_factory=dict)  # values may hold {url:<app>} (B17)
    # Host credential files mounted read-only into the build container (B36): container path -> host path.
    credentials: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Call:
    """An address this app carries of another app, by key, with its fallback (B17, B18)."""

    app: str
    key: str
    fallback: str


@dataclass(frozen=True)
class App:
    name: str
    repo: str
    kind: str  # "backend" | "frontend"
    service: str
    port: int
    artifact: str  # directory inside the worktree, relative
    build: Build
    forbidden: tuple[str, ...] = ()
    calls: tuple[Call, ...] = ()
    companions: tuple[str, ...] = ()
    scheduler_off: dict[str, str] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    suite: str | None = None
    suite_env: dict[str, str] = field(default_factory=dict)

    def fallbacks(self) -> dict[str, str]:
        """Callee app -> fallback address, from this app's `calls` (for expand())."""
        return {c.app: c.fallback for c in self.calls}


@dataclass(frozen=True)
class Runners:
    maven: str
    e2e: str
    allure: str


@dataclass(frozen=True)
class Workspace:
    root: Path
    cluster: Cluster
    address: AddressScheme
    paths: Paths
    repos: dict[str, Repo]
    apps: dict[str, App]
    runners: Runners

    @property
    def state_dir(self) -> Path:
        """`<workspace>/.dop`: overlays, locks, runs, builds."""
        return self.root / ".dop"

    def env(self) -> dict[str, str]:
        """The workspace env file (B5, B30). Values are secrets: never print them."""
        return parse_env_file(self.paths.env_file)

    def apps_of_repo(self, repo: str) -> list[App]:
        return [a for a in self.apps.values() if a.repo == repo]


# ---------------------------------------------------------------------------------------------
# locating and loading


def find_workspace(start: Path) -> Path:
    """The nearest ancestor of `start` (itself included) holding a dop.toml. Locates the
    workspace, never a demand (B7)."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG_NAME).is_file():
            return d
    raise UsageError(f"no {CONFIG_NAME} in {start} or any parent; pass --workspace")


def load(root: Path) -> Workspace:
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if not path.is_file():
        raise UsageError(f"{path}: not found")
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # includes a duplicated key or table
        raise UsageError(f"{path}: {exc}") from exc
    return _Parser(root, path).workspace(data)


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines and # comments ignored; optional `export `; one level of
    matching quotes stripped."""
    env: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _ENV_KEY.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key] = value
    return env


def url_refs(template: str) -> list[str]:
    """The app names a template references as {url:<app>}."""
    return _URL_REF.findall(template)


# ---------------------------------------------------------------------------------------------
# the strict parser


class _Parser:
    def __init__(self, root: Path, path: Path):
        self.root = root
        self.path = path

    def fail(self, where: str, msg: str) -> UsageError:
        return UsageError(f"{self.path}: {where}: {msg}")

    # -- primitives

    def table(self, data: dict, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict:
        if not isinstance(data, dict):
            raise self.fail(where, "must be a table")
        unknown = sorted(set(data) - set(required) - set(optional))
        if unknown:
            raise self.fail(where, f"unknown key(s): {', '.join(unknown)}")
        missing = [k for k in required if k not in data]
        if missing:
            raise self.fail(where, f"missing required key(s): {', '.join(missing)}")
        return data

    def string(self, value: Any, where: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise self.fail(where, "must be a non-empty string")
        return value

    def integer(self, value: Any, where: str, lo: int = 1, hi: int = 65535) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise self.fail(where, f"must be an integer in {lo}..{hi}")
        return value

    def strings(self, value: Any, where: str) -> tuple[str, ...]:
        if not isinstance(value, list):
            raise self.fail(where, "must be a list of strings")
        items = tuple(self.string(v, f"{where}[{i}]") for i, v in enumerate(value))
        dup = _first_duplicate(items)
        if dup is not None:
            raise self.fail(where, f"duplicated name {dup!r}")
        return items

    def str_map(self, value: Any, where: str) -> dict[str, str]:
        if not isinstance(value, dict):
            raise self.fail(where, "must be a table of strings")
        out: dict[str, str] = {}
        for k, v in value.items():
            if not _ENV_KEY.match(k):
                raise self.fail(where, f"{k!r} is not a valid variable name")
            if not isinstance(v, str):
                raise self.fail(f"{where}.{k}", "must be a string")
            out[k] = v
        return out

    def path_map(self, value: Any, where: str) -> dict[str, str]:
        """Container absolute path -> host path (B36)."""
        if not isinstance(value, dict):
            raise self.fail(where, "must be a table of strings")
        out: dict[str, str] = {}
        for k, v in value.items():
            if not k.startswith("/"):
                raise self.fail(where, f"{k!r} must be an absolute path in the container")
            if not isinstance(v, str) or not v:
                raise self.fail(f"{where}.{k}", "must be a host path")
            out[k] = v
        return out

    def rel_path(self, value: Any, where: str) -> str:
        s = self.string(value, where)
        p = Path(s)
        if p.is_absolute() or ".." in p.parts:
            raise self.fail(where, "must be a relative path inside the worktree")
        return s

    def ws_path(self, value: Any, where: str) -> Path:
        p = Path(self.string(value, where))
        return (p if p.is_absolute() else self.root / p).resolve()

    # -- sections

    def workspace(self, data: dict) -> Workspace:
        self.table(data, "top level", ("cluster", "address", "paths", "repos", "apps", "runners"))

        c = self.table(data["cluster"], "[cluster]", ("context", "node", "node_root"))
        node_root = self.string(c["node_root"], "cluster.node_root")
        if not node_root.startswith("/") or node_root.rstrip("/") == "":
            raise self.fail("cluster.node_root", "must be an absolute path other than /")
        cluster = Cluster(
            context=self.string(c["context"], "cluster.context"),
            node=self.string(c["node"], "cluster.node"),
            node_root=node_root.rstrip("/"),
        )

        a = self.table(data["address"], "[address]", ("domain", "port", "namespace_prefix", "shared_namespace"))
        domain = self.string(a["domain"], "address.domain")
        if domain != domain.lower():
            raise self.fail("address.domain", "must be lower case (B3)")
        prefix = self.string(a["namespace_prefix"], "address.namespace_prefix")
        shared = self.string(a["shared_namespace"], "address.shared_namespace")
        for where, v in (("address.namespace_prefix", prefix), ("address.shared_namespace", shared)):
            if not _DNS_LABEL.match(v):
                raise self.fail(where, "must be a lower-case DNS label")
        address = AddressScheme(domain, self.integer(a["port"], "address.port"), prefix, shared)

        p = self.table(data["paths"], "[paths]", ("manifests", "env_file", "reports", "test_root"))
        paths = Paths(
            manifests=self.ws_path(p["manifests"], "paths.manifests"),
            env_file=self.ws_path(p["env_file"], "paths.env_file"),
            reports=self.ws_path(p["reports"], "paths.reports"),
            test_root=self.ws_path(p["test_root"], "paths.test_root"),
        )
        if not paths.env_file.is_file():
            raise self.fail("paths.env_file", f"{paths.env_file} does not exist")

        repos_raw = data["repos"]
        if not isinstance(repos_raw, dict):
            raise self.fail("[repos]", "must be a table of tables")
        repos: dict[str, Repo] = {}
        for name, r in repos_raw.items():
            r = self.table(r, f"[repos.{name}]", ("dir",))
            repos[name] = Repo(name, self.ws_path(r["dir"], f"repos.{name}.dir"))
        dup = _first_duplicate([r.dir for r in repos.values()])
        if dup is not None:
            raise self.fail("[repos]", f"duplicated dir {dup}")

        apps_raw = data["apps"]
        if not isinstance(apps_raw, dict):
            raise self.fail("[apps]", "must be a table of tables")
        apps = {name: self.app(name, raw, repos) for name, raw in apps_raw.items()}
        self.cross_check(apps)

        r = self.table(data["runners"], "[runners]", ("maven", "e2e", "allure"))
        runners = Runners(
            maven=self.string(r["maven"], "runners.maven"),
            e2e=self.string(r["e2e"], "runners.e2e"),
            allure=self.string(r["allure"], "runners.allure"),
        )
        return Workspace(self.root, cluster, address, paths, repos, apps, runners)

    def app(self, name: str, raw: Any, repos: dict[str, Repo]) -> App:
        w = f"apps.{name}"
        if not _DNS_LABEL.match(name):
            raise self.fail(f"[{w}]", "an app name must be a lower-case DNS label (it is a hostname, B3)")
        t = self.table(
            raw, f"[{w}]",
            ("repo", "kind", "service", "port", "artifact", "build"),
            ("forbidden", "calls", "companions", "scheduler_off", "secrets", "suite", "suite_env"),
        )
        repo = self.string(t["repo"], f"{w}.repo")
        if repo not in repos:
            raise self.fail(f"{w}.repo", f"unknown repository {repo!r}")
        kind = self.string(t["kind"], f"{w}.kind")
        if kind not in KINDS:
            raise self.fail(f"{w}.kind", f"must be one of {', '.join(KINDS)}")
        service = self.string(t["service"], f"{w}.service")
        if not _DNS_LABEL.match(service):
            raise self.fail(f"{w}.service", "must be a lower-case DNS label")

        b = self.table(t["build"], f"{w}.build", ("image", "command"), ("env", "credentials"))
        cmd = b["command"]
        if isinstance(cmd, str):
            command: tuple[str, ...] = ("sh", "-c", self.string(cmd, f"{w}.build.command"))
        elif isinstance(cmd, list) and cmd:
            command = tuple(self.string(v, f"{w}.build.command[{i}]") for i, v in enumerate(cmd))
        else:
            raise self.fail(f"{w}.build.command", "must be a string or a non-empty list of strings")
        build = Build(
            image=self.string(b["image"], f"{w}.build.image"),
            command=command,
            env=self.str_map(b.get("env", {}), f"{w}.build.env"),
            credentials=self.path_map(b.get("credentials", {}), f"{w}.build.credentials"),
        )

        calls_raw = t.get("calls", [])
        if not isinstance(calls_raw, list):
            raise self.fail(f"{w}.calls", "must be a list of tables")
        calls = []
        for i, c in enumerate(calls_raw):
            cw = f"{w}.calls[{i}]"
            c = self.table(c, cw, ("app", "key", "fallback"))
            key = self.string(c["key"], f"{cw}.key")
            if not _ENV_KEY.match(key):
                raise self.fail(f"{cw}.key", f"{key!r} is not a valid variable name")
            calls.append(Call(self.string(c["app"], f"{cw}.app"), key, self.string(c["fallback"], f"{cw}.fallback")))
        dup = _first_duplicate([c.key for c in calls])
        if dup is not None:
            raise self.fail(f"{w}.calls", f"duplicated key {dup!r}")

        secrets = self.strings(t.get("secrets", []), f"{w}.secrets")
        for s in secrets:
            if not _ENV_KEY.match(s):
                raise self.fail(f"{w}.secrets", f"{s!r} is not a valid variable name")

        suite = t.get("suite")
        return App(
            name=name,
            repo=repo,
            kind=kind,
            service=service,
            port=self.integer(t["port"], f"{w}.port"),
            artifact=self.rel_path(t["artifact"], f"{w}.artifact"),
            build=build,
            forbidden=self.strings(t.get("forbidden", []), f"{w}.forbidden"),
            calls=tuple(calls),
            companions=self.strings(t.get("companions", []), f"{w}.companions"),
            scheduler_off=self.str_map(t.get("scheduler_off", {}), f"{w}.scheduler_off"),
            secrets=secrets,
            suite=None if suite is None else self.rel_path(suite, f"{w}.suite"),
            suite_env=self.str_map(t.get("suite_env", {}), f"{w}.suite_env"),
        )

    def cross_check(self, apps: dict[str, App]) -> None:
        """References between apps, and names that must be unique across apps."""
        for app in apps.values():
            w = f"apps.{app.name}"
            for comp in app.companions:
                if comp not in apps:
                    raise self.fail(f"{w}.companions", f"unknown app {comp!r}")
                if comp == app.name:
                    raise self.fail(f"{w}.companions", "an app cannot be its own companion")
            for c in app.calls:
                if c.app not in apps:
                    raise self.fail(f"{w}.calls", f"unknown app {c.app!r}")
            for where, tmpl_map in ((f"{w}.build.env", app.build.env), (f"{w}.suite_env", app.suite_env)):
                for key, tmpl in tmpl_map.items():
                    for ref in url_refs(tmpl):
                        if ref not in apps:
                            raise self.fail(f"{where}.{key}", f"{{url:{ref}}} names an unknown app")

        dup = _first_duplicate([a.service for a in apps.values()])
        if dup is not None:
            raise self.fail("[apps]", f"duplicated service name {dup!r}")
        dup = _first_duplicate([a.suite for a in apps.values() if a.suite])
        if dup is not None:
            raise self.fail("[apps]", f"duplicated suite {dup!r}")

        # scheduler_off keys all land in one ConfigMap per namespace (render): they cannot disagree.
        seen: dict[str, tuple[str, str]] = {}
        for app in apps.values():
            for k, v in app.scheduler_off.items():
                if k in seen and seen[k][1] != v:
                    raise self.fail(
                        f"apps.{app.name}.scheduler_off.{k}",
                        f"conflicts with apps.{seen[k][0]}.scheduler_off.{k}",
                    )
                seen.setdefault(k, (app.name, v))


def _first_duplicate(items):
    seen = set()
    for i in items:
        if i in seen:
            return i
        seen.add(i)
    return None
