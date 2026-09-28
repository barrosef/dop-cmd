"""The verb registry. The CLI is generated from it.

Each module in VERB_MODULES exports:
    VERB: VerbSpec
    run(ctx: Context) -> RunSummary
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from typing import Any, Callable

from ..context import Context
from ..lock import LockHeld
from ..outcome import RunSummary, Status, Unit, UnitResult, failed, skipped
from ..scope import Scope, resolve


@dataclass(frozen=True)
class Option:
    """A verb-specific command-line option, passed to argparse as add_argument(*flags, **kwargs).
    Its value reaches the verb as ctx.options[dest]."""

    flags: tuple[str, ...]
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class VerbSpec:
    name: str  # as typed: "up", "env up", "test e2e"
    help: str
    # the unit dimension (B13): "app" | "suite" | "repo" | "demand" | "shared"
    dimension: str
    # which of the common filters the verb takes: subset of ("tasks", "app", "repo")
    filters: tuple[str, ...] = ("tasks", "app")
    # --tasks may add demands not yet present (only `up`, B12)
    entering: bool = False
    # --tasks is mandatory (`up`, `down`)
    requires_tasks: bool = False
    options: tuple[Option, ...] = ()


VERB_MODULES = (
    "env_up", "up", "build", "deploy", "down", "status", "log",
    "test_aaa", "test_it", "test_e2e", "report",
)


def registry() -> dict[str, Any]:
    """Verb name -> module, in VERB_MODULES order."""
    mods = {}
    for m in VERB_MODULES:
        mod = importlib.import_module(f"{__name__}.{m}")
        mods[mod.VERB.name] = mod
    return mods


def act(ctx: Context, unit: Unit, fn: Callable[[], UnitResult], *, family: str | None = None) -> UnitResult:
    """Run one unit's work under its lock (B27). A lock held, or any exception, fails this unit
    only (B13). Verbs call this for every unit they act on.

    family (D13/B27 amended): units of the same (kind, demand, name) written by two different
    verbs -- `test aaa` and `test it` on the same repo -- are still one writer each, not one
    writer for both: it is folded into the *lock's* identity only (never into `unit` itself, so
    results and labels are unaffected)."""
    lock_unit = replace(unit, kind=f"{unit.kind}:{family}") if family else unit
    try:
        with ctx.lock(lock_unit):
            return fn()
    except LockHeld as exc:
        return failed(unit, str(exc))
    except Exception as exc:  # one unit never stops another
        return failed(unit, f"{type(exc).__name__}: {exc}")


def read(ctx: Context, unit: Unit, fn: Callable[[], UnitResult]) -> UnitResult:
    """Run one unit's read-only work (B27 amended): `status`, `log`, `report` take no lock --
    they change nothing, so they never contend with, or block, a writer."""
    try:
        return fn()
    except Exception as exc:  # one unit never stops another
        return failed(unit, f"{type(exc).__name__}: {exc}")


def summary_from(scope: Scope) -> RunSummary:
    """A summary opened with what resolution already decided."""
    return RunSummary(results=list(scope.skipped), note=scope.note)


def stub(ctx: Context, verb: VerbSpec) -> RunSummary:
    """Placeholder body until the verb is built: resolves the scope and skips every unit."""
    scope = resolve(ctx, verb)
    summary = summary_from(scope)
    for unit in scope.units:
        summary.add(skipped(unit, f"dop {verb.name} is not implemented yet"))
    return summary


__all__ = [
    "Option", "VerbSpec", "VERB_MODULES", "registry", "act", "read", "summary_from", "stub",
    "RunSummary", "Status",
]
