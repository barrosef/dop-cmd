import tempfile
import unittest
from pathlib import Path

from dop.git.branch_parser import parse_branch_table
from dop.core.errors import ValidationError


class TestBranchParser(unittest.TestCase):
    def _write_plan(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".md")
        path = Path(tmp.name)
        path.write_text(content, encoding="utf-8")
        tmp.close()
        return path

    def test_parse_branch_table(self):
        plan = self._write_plan(
            """
## Branch de Trabalho

| Repo | Branch |
|---|---|
| repo-one | OG-1-test |
| repo-two | OG-1-test |
""".lstrip()
        )
        result = parse_branch_table(plan)
        self.assertEqual(result["repo-one"], "OG-1-test")
        self.assertEqual(result["repo-two"], "OG-1-test")
        plan.unlink()

    def test_missing_section(self):
        plan = self._write_plan("# Outro titulo\n")
        with self.assertRaises(ValidationError):
            parse_branch_table(plan)
        plan.unlink()

    def test_empty_section(self):
        plan = self._write_plan(
            """
## Branch de Trabalho

| Repo | Branch |
|---|---|
""".lstrip()
        )
        with self.assertRaises(ValidationError):
            parse_branch_table(plan)
        plan.unlink()


if __name__ == "__main__":
    unittest.main()
