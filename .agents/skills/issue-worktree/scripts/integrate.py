#!/usr/bin/env python3
"""Serialize verified issue integration and normal pushes to a personal jobs branch."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Filesystems shared from another kernel (Docker Desktop/WSL host drives). A flock taken
# there is invisible to integrators on the host that owns the checkout.
HOST_SHARED_FILESYSTEMS = {"9p", "drvfs", "fakeowner", "fuse.grpcfuse", "virtiofs"}


def lock_exclusive(lock):
    """Take the non-blocking, process-lifetime lock with the host's native API."""
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BlockingIOError from None
    else:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def mount_filesystem(path, mountinfo):
    """Return the filesystem type of the deepest mount containing path."""
    best, kind = "", None
    for line in mountinfo.splitlines():
        fields = line.split(" - ")
        mount_point = fields[0].split()[4].replace("\\040", " ")
        inside = path == mount_point or path.startswith(mount_point.rstrip("/") + "/")
        if inside and len(mount_point) > len(best):
            best, kind = mount_point, fields[1].split()[0]
    return kind


def check_shell(git):
    """Use Git Bash on Windows: a bare 'bash' may resolve to WSL in System32."""
    if os.name != "nt":
        return "bash"
    bash = Path(git("--exec-path", capture=True)).parents[2] / "bin" / "bash.exe"
    if not bash.is_file():
        raise RuntimeError(f"Git Bash not found at {bash}")
    return str(bash)


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
    if Path("/proc/self/mountinfo").exists():
        kind = mount_filesystem(
            str(common.resolve()), Path("/proc/self/mountinfo").read_text()
        )
        require(
            kind not in HOST_SHARED_FILESYSTEMS,
            f"Repository is on a host-shared {kind} filesystem; "
            "run integration on the host that owns it",
        )
    shell = check_shell(git)
    with (common / "issue-integration.lock").open("a") as lock:
        try:
            lock_exclusive(lock)
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
                [shell, "-o", "pipefail", "-c", command], cwd=root, check=True
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
