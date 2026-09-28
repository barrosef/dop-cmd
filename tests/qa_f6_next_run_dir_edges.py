"""QA F6 — `next_run_dir` edge cases (B16: nothing deletes or reuses a report run).

Oracle: docs/business-rules.md B16. Scope: FRONT — src/dop/verbs/_testing.py (next_run_dir).
Pure unit tests, no fakes, no filesystem outside tmp_path. These are the "does it break" probes
from the brief (mixed run-N widths, a non-directory named run-N); recorded here as green because
reading + testing found the numeric parse (`int(m.group(1))`) already immune to the lexicographic
trap, not because the area was skipped.
"""

from __future__ import annotations

from pathlib import Path

from dop.verbs._testing import next_run_dir


def test_mixed_width_run_dirs_are_compared_numerically_not_lexically(tmp_path: Path):
    proj = tmp_path / "proj"
    for name in ("run-1", "run-01", "run-10", "run-2"):
        (proj / name).mkdir(parents=True)
    # A naive string/lexicographic max would pick "run-2" > "run-10" and hand back "run-3",
    # re-issuing a number already on disk (B16: never reuses one). The actual code parses the
    # numeric group, so it must land on run-11.
    assert next_run_dir(proj).name == "run-11"


def test_a_file_named_run_n_does_not_corrupt_or_get_reused(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "run-5").write_text("not a directory")  # stray/foreign file, same naming shape
    (proj / "run-3").mkdir()
    # The regex matches the name regardless of file-vs-dir, so the stray file still counts toward
    # the max (5) rather than being silently reused or ignored in a way that collides with it.
    next_dir = next_run_dir(proj)
    assert next_dir.name == "run-6"
    assert not next_dir.exists()  # next_run_dir only computes the path; nothing is created yet


def test_next_run_dir_never_points_at_an_existing_run(tmp_path: Path):
    proj = tmp_path / "proj"
    for name in ("run-1", "run-2", "run-3"):
        (proj / name).mkdir(parents=True)
    nxt = next_run_dir(proj)
    assert not nxt.exists()
    assert nxt.name == "run-4"
