#!/usr/bin/env python3
"""Serialize verified issue integration and normal pushes to a personal jobs branch."""

import argparse
import fcntl
import re
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--message-file", type=Path, required=True)
    parser.add_argument("--check", action="append", required=True)
    args = parser.parse_args()
    root = args.integration.resolve(strict=True)
    message = args.message_file.resolve(strict=True).read_text()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-jobs", args.branch):
        parser.error("Integration branch must be a personal name-jobs branch")
    if args.feature == args.branch or not message.strip():
        parser.error(
            "A separate feature branch and nonempty merge message are required"
        )

    def git(*argv, capture=False, check=True):
        result = subprocess.run(
            ["git", "-C", str(root), *argv],
            text=True,
            capture_output=capture,
            check=check,
        )
        return result.stdout.strip() if capture else result.returncode

    def require(condition, explanation):
        if not condition:
            raise RuntimeError(explanation)

    common = Path(
        git("rev-parse", "--path-format=absolute", "--git-common-dir", capture=True)
    )
    with (common / "issue-integration.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another agent holds the integration lock; retry later"
            ) from None
        require(
            git("branch", "--show-current", capture=True) == args.branch,
            "Integration checkout is on a different branch",
        )
        require(
            not git("status", "--porcelain", capture=True),
            "Integration checkout is dirty; inspect existing work or pending merge",
        )
        merge_head = Path(
            git(
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "MERGE_HEAD",
                capture=True,
            )
        )
        require(not merge_head.exists(), "An existing merge needs recovery")
        git("check-ref-format", "--branch", args.feature)
        feature = git(
            "rev-parse", "--verify", f"refs/heads/{args.feature}", capture=True
        )
        git(
            "fetch",
            "origin",
            f"+refs/heads/{args.branch}:refs/remotes/origin/{args.branch}",
        )
        remote = f"refs/remotes/origin/{args.branch}"
        git("merge", "--ff-only", remote)
        require(
            git("rev-parse", "HEAD", capture=True)
            == git("rev-parse", remote, capture=True),
            "Unpublished integration commits exist; inspect and recover before another merge",
        )
        if git("merge-base", "--is-ancestor", feature, "HEAD", check=False) == 0:
            print(
                "Feature already published on the jobs branch; no changes needed",
                flush=True,
            )
            return
        git("merge", "--no-ff", "--no-commit", feature)
        merged_tree = git("write-tree", capture=True)
        for command in args.check:
            require(bool(command.strip()), "Empty check command")
            print(f"Running integration check: {command}", flush=True)
            subprocess.run(
                ["bash", "-o", "pipefail", "-c", command], cwd=root, check=True
            )
        require(
            git("diff", "--quiet", check=False) == 0,
            "Checks modified tracked files; inspect before committing",
        )
        require(
            git("write-tree", capture=True) == merged_tree,
            "Checks modified the staged merge; inspect before committing",
        )
        require(
            not git("ls-files", "--others", "--exclude-standard", capture=True),
            "Checks left untracked files; inspect before committing",
        )
        subprocess.run(
            ["git", "-C", str(root), "commit", "--file=-"],
            input=message,
            text=True,
            check=True,
        )
        commit = git("rev-parse", "HEAD", capture=True)
        require(
            git("rev-parse", "HEAD^{tree}", capture=True) == merged_tree,
            "Commit hook changed the tested tree; inspect before pushing",
        )
        require(
            not git("status", "--porcelain", capture=True),
            "Commit hook left changes; inspect before pushing",
        )
        git("push", "origin", f"refs/heads/{args.branch}:refs/heads/{args.branch}")
        published = git(
            "ls-remote", "--heads", "origin", f"refs/heads/{args.branch}", capture=True
        )
        require(
            bool(published) and published.split()[0] == commit,
            "Remote tip changed or verification failed; fetch and verify tested commit ancestry",
        )
        print(f"Published {commit} to origin/{args.branch}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(
            f"Integration stopped: {error}\nInspect state; no automatic reset, abort or force push was performed.",
            file=sys.stderr,
        )
        sys.exit(1)
