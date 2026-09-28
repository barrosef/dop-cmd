"""dop build — build each app's artifact in the demand's own tree (§5, B17, B31).

A worktree app builds in place, in its own worktree (unique to the demand — no two demands ever
share it). A companion (B19) is copied from the main checkout into
`<workspace>/.dop/builds/<DEMAND>/<app>/` and built there (B31), so a companion build never touches
the checkout other demands read. The copy takes only what git tracks plus what it does not ignore
(`git ls-files --cached --others --exclude-standard`, read-only — D10): an ignored file such as
`.env.local` never enters the build directory, matching the companion's clean/dirty label.

Either way the build runs in a host container (B21): the build directory mounted, `build.image`/
`build.command` run in it, as the invoking user — never root (D9/B42). `HOME` is repointed at a
directory that user can actually write to (`/tmp`), which is also where the shared Maven cache
mounts (`config.MAVEN_CACHE_PATH`, under `HOME` so Maven's default `${user.home}/.m2/repository`
finds it without needing `-Dmaven.repo.local`); the cache volume itself must exist writable by that
uid — `docker volume create` defaults to root ownership, so the workspace bootstraps it once
(`docker run --rm -v dop-maven-cache:/tmp/.m2 --user 0 busybox chown <uid>:<gid> /tmp/.m2`, or an
equivalent one-off step outside `dop`) before the first build. A front-end's `node_modules` simply
stays inside the directory that was built (no cache).

Front-end `build.env` is expanded to the demand's address of the apps it calls, or their declared
fallback when an app is outside the demand (B17). After the container exits 0, the artifact is
validated (D8/B41): a back-end must hold exactly one runnable jar (`*-sources.jar`, `*-javadoc.jar`
and `original-*.jar` do not count), a front-end must hold `index.html`; a build with no valid
artifact fails the unit. A front-end's artifact is then scanned for every string `forbidden` names,
including inside a `.gz` (decompressed in memory) or `.br` payload — when the `brotli` module is not
installed, `.br` files are skipped with a note, never silently treated as clean (D19); a hit fails
the unit, naming the file and the string.
"""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import TextIO

from ..address import expand
from ..config import BUILD_WORKDIR, MAVEN_CACHE_PATH, App, Workspace
from ..context import Context
from ..outcome import RunSummary, Unit, UnitResult, done, failed, planned
from ..runner import Mount
from ..scope import SOURCE_TRUNK, demand_apps, resolve
from . import VerbSpec, act, summary_from

VERB = VerbSpec(
    name="build",
    help="build each application's artifact in a host build container",
    dimension="app",
    filters=("tasks", "app"),
)

# Not fixed by business-rules.md or spec.md: a shared cache keeps every backend's Maven downloads
# across demands (the build output itself never is). The paths themselves are config's (B43 checks
# a credential is never mounted inside either).
_MAVEN_CACHE_VOLUME = "dop-maven-cache"
_MAVEN_CACHE_PATH = MAVEN_CACHE_PATH
_WORKDIR = BUILD_WORKDIR
_HOME = "/tmp"  # writable by any uid (D9/B42) — HOME so npm's and Maven's caches resolve under it


def build_dir(ws: Workspace, unit: Unit) -> Path:
    """Where a unit's build runs (B31): the worktree itself, or the companion's own copy."""
    if unit.source == SOURCE_TRUNK:
        return ws.state_dir / "builds" / unit.demand / unit.name
    return unit.path


def artifact_dir(ws: Workspace, app: App, unit: Unit) -> Path:
    """Where the built artifact lands — deploy locates it the same way (B31)."""
    return build_dir(ws, unit) / app.artifact


def _tracked_files(src: Path) -> list[str]:
    """What git says belongs in a copy of `src` (D10): tracked, plus untracked-but-not-ignored.
    Read-only — never touches the checkout's index or working tree."""
    proc = subprocess.run(
        ["git", "-C", str(src), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"git ls-files in {src} failed" + (f": {detail}" if detail else ""))
    text = proc.stdout.decode("utf-8", "surrogateescape")
    return [rel for rel in text.split("\0") if rel]


def _force_remove(func, path, exc_info) -> None:
    """`shutil.rmtree` onerror: a previous companion build ran as root and left read-only or
    unremovable entries behind (rootful docker). Deleting an entry needs write permission on its
    *parent* (that is what actually blocks the unlink/rmdir here), so reclaim that first, then the
    entry itself in case it is a directory rmtree still has to descend into, and retry once."""
    p = Path(path)
    for target in (p.parent, p):
        try:
            os.chmod(target, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
        except OSError:
            pass
    func(path)


def _copy_checkout(src: Path, dest: Path) -> None:
    """A companion's build source: exactly what git tracks or leaves untracked-and-unignored in
    the main checkout (D10) — never an ignored file such as `.env.local`."""
    if dest.exists():
        shutil.rmtree(dest, onerror=_force_remove)
    dest.mkdir(parents=True)
    for rel in _tracked_files(src):
        s = src / rel
        if not s.is_file():
            continue
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)


def build_env(ws: Workspace, app: App, unit: Unit) -> dict[str, str]:
    """Front-end build.env, expanded to the demand's address of each app it calls, or that app's
    declared fallback when it is outside the demand (B17)."""
    in_demand_units, _ = demand_apps(ws, unit.demand)
    in_demand = {u.name for u in in_demand_units}
    fallbacks = app.fallbacks()
    return {key: expand(tmpl, ws, unit.demand, apps=in_demand, fallbacks=fallbacks)
            for key, tmpl in app.build.env.items()}


def _payloads(path: Path, data: bytes, out: TextIO | None) -> list[bytes]:
    """`data`, plus what it decompresses to when the forbidden scan cannot trust the raw bytes
    (D19): a `.gz` is decompressed in memory; a `.br` likewise when `brotli` is installed, else the
    file is skipped for decompression with a note — never silently treated as scanned-and-clean."""
    if path.suffix == ".gz":
        try:
            return [data, gzip.decompress(data)]
        except OSError:
            return [data]
    if path.suffix == ".br":
        try:
            import brotli
        except ImportError:
            if out is not None:
                print(f"note: brotli not installed, {path.name} not decompressed for the forbidden scan",
                      file=out)
            return [data]
        try:
            return [data, brotli.decompress(data)]
        except Exception:
            return [data]
    return [data]


def _scan_forbidden(app: App, artifact: Path, out: TextIO | None = None) -> str | None:
    """The first forbidden string found in the artifact, naming the file (B17, D19)."""
    for path in sorted(p for p in artifact.rglob("*") if p.is_file()):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for payload in _payloads(path, data, out):
            for needle in app.forbidden:
                if needle.encode("utf-8") in payload:
                    return f"{path.relative_to(artifact)}: contains forbidden string {needle!r}"
    return None


def _validate_artifact(app: App, artifact: Path) -> str | None:
    """Build fails when it produced no valid artifact (D8/B41): a back-end must hold exactly one
    runnable jar (sources/javadoc/original-* excluded); a front-end must hold `index.html`."""
    if app.kind == "frontend":
        if not (artifact / "index.html").is_file():
            return f"no index.html in {artifact}"
        return None
    if not artifact.is_dir():
        return f"no artifact directory {artifact}"
    jars = [p for p in artifact.glob("*.jar")
            if not p.stem.endswith("-sources") and not p.stem.endswith("-javadoc")
            and not p.name.startswith("original-")]
    if len(jars) != 1:
        return f"expected exactly one runnable jar in {artifact}, found {len(jars)}: " \
               f"{', '.join(p.name for p in jars) or '(none)'}"
    return None


def _run_one(ctx: Context, unit: Unit) -> UnitResult:
    ws = ctx.ws
    app = ws.apps[unit.name]
    dest = build_dir(ws, unit)

    if unit.source == SOURCE_TRUNK:
        if ctx.dry_run:
            print(f"[dry-run] copy {unit.path} -> {dest} (git-tracked and untracked-unignored files only)",
                  file=ctx.out)
        else:
            _copy_checkout(unit.path, dest)

    env = {**(build_env(ws, app, unit) if app.kind == "frontend" else {}), "HOME": _HOME}
    mounts = [Mount(dest, _WORKDIR)]
    if app.kind == "backend":
        mounts.append(Mount(_MAVEN_CACHE_VOLUME, _MAVEN_CACHE_PATH))
    for dst, src in app.build.credentials.items():  # B36/B43: read-only, never copied, never printed
        host = Path(src)  # already absolute (config resolved it against the workspace, D11/B43)
        if not host.is_file():
            raise RuntimeError(f"credential file {src} (for {dst}) does not exist on this host")
        mounts.append(Mount(host, dst, readonly=True))

    proc = ctx.runner.run(
        app.build.image, mounts=mounts, env=env, workdir=_WORKDIR, command=app.build.command,
        user=f"{os.getuid()}:{os.getgid()}",  # never root (D9/B42)
        capture=True,
    )

    if ctx.dry_run:
        return planned(unit, f"would build in {dest}")

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return failed(unit, f"build failed (exit {proc.returncode})" + (f": {detail}" if detail else ""))

    artifact = artifact_dir(ws, app, unit)
    bad = _validate_artifact(app, artifact)
    if bad:
        return failed(unit, bad)

    if app.kind == "frontend":
        hit = _scan_forbidden(app, artifact, ctx.out)
        if hit:
            return failed(unit, hit)

    return done(unit, f"built in {artifact}")


def run(ctx: Context) -> RunSummary:
    scope = resolve(ctx, VERB)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(act(ctx, unit, lambda unit=unit: _run_one(ctx, unit)))
    return summary
