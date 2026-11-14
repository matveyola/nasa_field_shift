"""Exercise integration against temporary local repositories, never live remotes."""

import fcntl
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).with_name("integrate.py")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "integration"
        self.feature = self.root / "feature"
        self.run_command("git", "init", "--bare", str(self.remote))
        self.run_command("git", "init", "-b", "nazar-jobs", str(self.repo))
        self.git("config", "user.name", "Integration Test Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "base.txt").write_text("base\n")
        self.git("add", ".")
        self.git("commit", "-m", "fixture base")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "nazar-jobs")
        self.base = self.remote_tip()
        self.git("worktree", "add", "-b", "nazar-issue-3", str(self.feature))
        (self.feature / "feature.txt").write_text("feature\n")
        self.git("add", ".", cwd=self.feature)
        self.git("commit", "-m", "fixture feature", cwd=self.feature)
        self.feature_tip = self.git("rev-parse", "HEAD", cwd=self.feature)
        self.message = self.root / "message.txt"
        self.message.write_text(
            "merge: fixture issue\n\nVerify isolated Git integration.\n"
        )

    def run_command(self, *argv, cwd=None, check=True):
        return subprocess.run(
            argv, cwd=cwd, text=True, capture_output=True, check=check
        )

    def git(self, *argv, cwd=None):
        return self.run_command("git", *argv, cwd=cwd or self.repo).stdout.strip()

    def remote_tip(self):
        return self.git(
            "--git-dir", str(self.remote), "rev-parse", "refs/heads/nazar-jobs"
        )

    def integrate(
        self, check="test -f base.txt && test -f feature.txt", branch="nazar-jobs"
    ):
        return self.run_command(
            sys.executable,
            str(HELPER),
            "--integration",
            str(self.repo),
            "--branch",
            branch,
            "--feature",
            "nazar-issue-3",
            "--message-file",
            str(self.message),
            "--check",
            check,
            check=False,
        )

    def assert_blocked(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_tip(), self.base)

    def test_success_publishes_merge_with_both_parents(self):
        result = self.integrate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        tip = self.remote_tip()
        self.assertEqual(self.git("rev-parse", "HEAD"), tip)
        self.assertEqual(self.git("rev-parse", "HEAD^1"), self.base)
        self.assertEqual(self.git("rev-parse", "HEAD^2"), self.feature_tip)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.integrate().returncode, 0)
        self.assertEqual(self.remote_tip(), tip)

    def test_failed_check_never_publishes_and_preserves_merge(self):
        self.assert_blocked(self.integrate("exit 23"))
        self.assertEqual(self.git("rev-parse", "MERGE_HEAD"), self.feature_tip)

    def test_dirty_integration_checkout_is_preserved(self):
        (self.repo / "base.txt").write_text("human change\n")
        self.assert_blocked(self.integrate())
        self.assertEqual((self.repo / "base.txt").read_text(), "human change\n")

    def test_shared_lock_blocks_other_agent(self):
        with (self.repo / ".git" / "issue-integration.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_blocked(self.integrate())
        self.assertEqual(self.integrate().returncode, 0)

    def test_main_is_never_an_integration_target(self):
        self.assert_blocked(self.integrate(branch="main"))

    def test_checks_cannot_silently_change_tested_tree(self):
        self.assert_blocked(
            self.integrate("printf changed > feature.txt && git add feature.txt")
        )

    def test_unpublished_commits_require_recovery(self):
        (self.repo / "local.txt").write_text("unpublished\n")
        self.git("add", ".")
        self.git("commit", "-m", "fixture unpublished work")
        self.assert_blocked(self.integrate())

    def other_checkout(self):
        other = self.root / "other"
        self.run_command(
            "git", "clone", "-b", "nazar-jobs", str(self.remote), str(other)
        )
        self.git("config", "user.name", "Other Test Fixture", cwd=other)
        self.git("config", "user.email", "fixture@example.invalid", cwd=other)
        (other / "remote.txt").write_text("remote addition\n")
        self.git("add", ".", cwd=other)
        self.git("commit", "-m", "fixture remote addition", cwd=other)
        return other

    def test_new_remote_work_is_included_before_checks(self):
        other = self.other_checkout()
        self.git("push", "origin", "nazar-jobs", cwd=other)
        result = self.integrate("test -f remote.txt && test -f feature.txt")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.repo / "remote.txt").exists())

    def test_remote_race_rejects_push_without_overwriting(self):
        other = self.other_checkout()
        other_tip = self.git("rev-parse", "HEAD", cwd=other)
        result = self.integrate(
            f"git -C {shlex.quote(str(other))} push origin nazar-jobs"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_tip(), other_tip)
        self.assertEqual(self.git("rev-parse", "HEAD^2"), self.feature_tip)

    def test_conflict_is_preserved_and_not_pushed(self):
        (self.feature / "base.txt").write_text("feature edit\n")
        self.git("add", ".", cwd=self.feature)
        self.git("commit", "-m", "fixture conflicting feature", cwd=self.feature)
        (self.repo / "base.txt").write_text("integration edit\n")
        self.git("add", ".")
        self.git("commit", "-m", "fixture conflicting integration")
        self.git("push", "origin", "nazar-jobs")
        self.base = self.remote_tip()
        self.assert_blocked(self.integrate())
        self.assertIn("UU base.txt", self.git("status", "--porcelain"))


if __name__ == "__main__":
    unittest.main()
