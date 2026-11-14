"""Offline backup of the full git history as a verified bundle. Never pushes, never rewrites history.

    uv run python scripts/backup.py --dest D:/field-shift-backups

Writes <dest>/field-shift-agent-<short HEAD>.bundle (all branches and tags; only committed work: the
bundle never contains untracked or ignored files such as .env, data/, model weights or node_modules),
then verifies it: `git bundle verify`, the expected branch and tags are present, and a test restore
into a temporary directory reproduces the same commits. Prints the SHA-256 to record next to the copy.

Restore on any machine:
    git clone <file>.bundle field-shift-agent-restored
"""

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", required=True, help="directory for the bundle (prefer another drive or private storage)")
    args = parser.parse_args()
    dest = Path(args.dest).resolve()
    if ROOT in dest.parents or dest == ROOT:
        raise SystemExit("choose a destination outside the repository")
    dest.mkdir(parents=True, exist_ok=True)

    head = git("rev-parse", "--short", "HEAD")
    refs = dict(line.split(" ", 1)[::-1] for line in git("show-ref").splitlines())  # ref -> sha
    if git("status", "--porcelain"):
        print("note: uncommitted changes exist and are NOT in the bundle (commit first to include them)")
    bundle = dest / f"field-shift-agent-{head}.bundle"
    if bundle.exists():
        raise SystemExit(f"{bundle} already exists: not overwriting")
    git("bundle", "create", str(bundle), "--all")

    git("bundle", "verify", str(bundle))
    heads = dict(line.split(" ", 1)[::-1] for line in git("bundle", "list-heads", str(bundle)).splitlines())
    missing = {ref for ref in refs if ref not in heads or heads[ref] != refs[ref]}
    if missing:
        raise SystemExit(f"bundle is missing or differs for: {sorted(missing)}")
    with tempfile.TemporaryDirectory() as tmp:
        restored = Path(tmp) / "restored"
        git("clone", "--quiet", str(bundle), str(restored), cwd=Path(tmp))
        if git("rev-parse", "HEAD", cwd=restored) != git("rev-parse", "HEAD"):
            raise SystemExit("restored HEAD differs from the repository HEAD")
        tags = git("tag", cwd=restored).split()
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    print(f"bundle:   {bundle}")
    print(f"size:     {bundle.stat().st_size} bytes")
    print(f"sha256:   {digest}")
    print("refs:     " + ", ".join(f"{ref} {sha[:7]}" for ref, sha in sorted(heads.items())))
    print(f"restore:  verified (HEAD {head}, tags {tags})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
