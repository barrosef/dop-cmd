import tempfile
import unittest
from pathlib import Path

from dop.core.errors import StateError, ValidationError
from dop.core.state import (
    add_linked_jira,
    append_command_log,
    append_pr_record,
    impacted_repos,
    load_state,
    resolve_alias,
    save_state,
    set_repo_impacted,
    set_repo_skipped,
    write_alias,
)


class TestStateBasics(unittest.TestCase):
    def test_default_state_creates_file_and_has_minimal_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / ".state.json"
            state, original = load_state(
                "OG-101", state_path,
                jira_base_url="https://example.test/browse",
            )
            self.assertEqual(state["jiraKey"], "OG-101")
            self.assertEqual(state["jiraUrl"], "https://example.test/browse/OG-101")
            self.assertEqual(state["linkedJiraKeys"], [])
            self.assertEqual(state["repos"], {})
            self.assertEqual(state["prs"], [])
            self.assertEqual(state["commands_log"], [])
            self.assertNotIn("stage", state)
            self.assertNotIn("artifacts", state)
            self.assertTrue(state_path.exists())

    def test_set_repo_impacted_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / ".state.json"
            state, original = load_state("OG-200", state_path, jira_base_url="https://x/browse")
            set_repo_impacted(state, "lifesupport-api", "OG-200-feature")
            set_repo_impacted(state, "optum-support-be", "OG-200-feature")
            set_repo_skipped(state, "providers-back-end")
            save_state(state_path, state, original)
            self.assertEqual(set(impacted_repos(state)), {"lifesupport-api", "optum-support-be"})

    def test_commands_log_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / ".state.json"
            state, original = load_state("OG-300", state_path, jira_base_url="https://x/browse")
            append_command_log(state, "demand-init OG-300 --repos lifesupport-api")
            save_state(state_path, state, original)

            state2, original2 = load_state("OG-300", state_path, jira_base_url="https://x/browse")
            state2["commands_log"][0]["command"] = "tampered"
            with self.assertRaises(StateError):
                save_state(state_path, state2, original2)

    def test_invalid_jira_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValidationError):
                load_state("nope", Path(tmp) / ".state.json", jira_base_url="https://x/browse")

    def test_pr_record_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / ".state.json"
            state, original = load_state("OG-400", state_path, jira_base_url="https://x/browse")
            append_pr_record(state, {"repo": "lifesupport-api", "pr_id": "1"})
            append_pr_record(state, {"repo": "lifesupport-api", "pr_id": "2"})
            self.assertEqual(len(state["prs"]), 2)
            self.assertIn("createdAt", state["prs"][0])


class TestLinkedAndAlias(unittest.TestCase):
    def test_add_linked_jira_dedup(self):
        state = {"jiraKey": "OG-1550", "linkedJiraKeys": []}
        self.assertTrue(add_linked_jira(state, "OG-1551"))
        self.assertFalse(add_linked_jira(state, "OG-1551"))  # já existe
        self.assertFalse(add_linked_jira(state, "OG-1550"))  # é a própria mestre
        self.assertEqual(state["linkedJiraKeys"], ["OG-1551"])

    def test_alias_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_alias(root, "OG-3030", "OG-1550")
            self.assertEqual(resolve_alias(root, "OG-3030"), "OG-1550")
            self.assertEqual(resolve_alias(root, "OG-1550"), "OG-1550")  # sem alias


if __name__ == "__main__":
    unittest.main()
