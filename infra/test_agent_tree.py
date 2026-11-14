"""Exercise invalid configurations against a disposable copy of the actual setup."""

import shutil
import tempfile
import unittest
from pathlib import Path

from check_agent_tree import ROOT, validate


class AgentTreeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in (".codex", ".agents", "docs", "infra"):
            shutil.copytree(ROOT / directory, self.root / directory)
        shutil.copy2(ROOT / "AGENTS.md", self.root / "AGENTS.md")

    def test_repository_configuration(self):
        self.assertEqual(validate(self.root), [])

    def test_missing_role_is_rejected(self):
        (self.root / ".codex/agents/verifier.toml").unlink()
        self.assertTrue(any("inventory" in error for error in validate(self.root)))

    def test_invalid_toml_is_reported(self):
        (self.root / ".codex/agents/verifier.toml").write_text("invalid = [")
        self.assertTrue(any("verifier.toml" in error for error in validate(self.root)))

    def test_duplicate_role_is_rejected(self):
        shutil.copy2(
            self.root / ".codex/agents/verifier.toml",
            self.root / ".codex/agents/duplicate.toml",
        )
        self.assertTrue(any("duplicate role" in error for error in validate(self.root)))

    def test_missing_instructions_is_rejected(self):
        (self.root / ".codex/agents/verifier.toml").write_text(
            'name = "verifier"\ndescription = "Review"\n'
        )
        self.assertTrue(any("developer_instructions" in error for error in validate(self.root)))

    def test_permission_override_is_rejected(self):
        path = self.root / ".codex/agents/verifier.toml"
        path.write_text(path.read_text() + '\nsandbox_mode = "danger-full-access"\n')
        self.assertTrue(any("parent session" in error for error in validate(self.root)))

    def test_bad_capacity_is_rejected(self):
        path = self.root / ".codex/config.toml"
        for value in ("0", "9", "true", '"eight"'):
            with self.subTest(value=value):
                path.write_text(
                    f"[agents]\nenabled = true\nmax_concurrent_threads_per_session = {value}\n"
                )
                self.assertTrue(any("concurrency" in error for error in validate(self.root)))

    def test_broken_workflow_link_is_rejected(self):
        path = self.root / ".agents/skills/agent-tree/SKILL.md"
        path.write_text(path.read_text() + "\n[missing](missing.md)\n")
        self.assertTrue(any("broken link" in error for error in validate(self.root)))


if __name__ == "__main__":
    unittest.main()
