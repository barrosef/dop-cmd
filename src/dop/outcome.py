"""Units, their results, the run summary and the exit contract (B13, B14, B15)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3


class UsageError(Exception):
    """A usage or configuration error: nothing was attempted (exit 2)."""


class Status(enum.Enum):
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    # Dry-run only (B15): the unit resolved and would be acted on; no outcome is claimed.
    PLANNED = "planned"


@dataclass(frozen=True)
class Unit:
    """One thing a command acts on (B13).

    kind is the dimension: "app" (demand, app), "suite" (demand, suite), "repo" (demand, repo),
    "demand" (the demand itself) or "shared" (the shared services; demand is None).
    Resolution detail (repo, source, path) does not take part in identity.
    """

    kind: str
    demand: str | None
    name: str | None = None
    repo: str | None = field(default=None, compare=False)
    # "worktree" (the demand's own worktree) or "trunk" (a companion from the main checkout, B19).
    source: str | None = field(default=None, compare=False)
    # The directory the unit's code comes from: the demand's worktree, or the main checkout.
    path: Path | None = field(default=None, compare=False)
    # For a companion (source "trunk"): the main checkout's branch@sha[ dirty] (B31).
    ref: str | None = field(default=None, compare=False)
    # For a suite unit: the app the suite belongs to.
    app: str | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        parts = [p for p in (self.demand, self.name) if p]
        return "/".join(parts) if parts else self.kind


@dataclass(frozen=True)
class UnitResult:
    unit: Unit
    status: Status
    reason: str = ""


def done(unit: Unit, reason: str = "") -> UnitResult:
    return UnitResult(unit, Status.DONE, reason)


def skipped(unit: Unit, reason: str) -> UnitResult:
    return UnitResult(unit, Status.SKIPPED, reason)


def failed(unit: Unit, reason: str) -> UnitResult:
    return UnitResult(unit, Status.FAILED, reason)


def planned(unit: Unit, reason: str = "") -> UnitResult:
    return UnitResult(unit, Status.PLANNED, reason)


@dataclass
class RunSummary:
    """Every unit's result, plus a note when there was nothing to do (B11)."""

    results: list[UnitResult] = field(default_factory=list)
    note: str = ""

    def add(self, result: UnitResult) -> UnitResult:
        self.results.append(result)
        return result

    def extend(self, results) -> None:
        self.results.extend(results)

    def of(self, status: Status) -> list[UnitResult]:
        return [r for r in self.results if r.status is status]

    def exit_code(self) -> int:
        """B14: 1 anything failed · 3 something skipped or nothing to do · 0 all done.

        A dry-run's planned units count as done: resolution succeeded, nothing was attempted.
        """
        if self.of(Status.FAILED):
            return EXIT_FAILED
        if not self.results or self.of(Status.SKIPPED):
            return EXIT_PARTIAL
        return EXIT_DONE

    def render(self, out: TextIO) -> None:
        """The closing summary: counts, then every non-done unit with its reason (B13)."""
        if self.note:
            print(self.note, file=out)
        counts = ", ".join(
            f"{len(self.of(s))} {s.value}" for s in Status if self.of(s)
        ) or "0 units"
        print(f"summary: {counts}", file=out)
        for r in self.results:
            if r.status is Status.DONE:
                continue
            reason = f": {r.reason}" if r.reason else ""
            print(f"  {r.status.value:<8} {r.unit.label}{reason}", file=out)
