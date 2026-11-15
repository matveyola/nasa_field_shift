#!/usr/bin/env python3
"""Run the API fast suite with Linux semantics in a network-isolated container.

The container tests the checkout's staged tree (git write-tree), streamed in with
git archive, so the result does not depend on working-tree line endings. That is the
tree integrate.py commits. The host downloads Linux wheels for the locked API
requirements into a cache outside the checkout. The container gets that cache read-only,
no network, no credentials and no published ports, and is removed afterwards. Use it as
an integrate.py check on hosts without a native Linux userland.
"""

import argparse
import hashlib
import lzma
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

IMAGE = "python:3.12-slim"
PLATFORMS = ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64")
DEBIAN = "https://deb.debian.org/debian"
# Rasterio's bundled GDAL needs the system libexpat1, as in the API image. The container
# verifies the signed Debian index with the image's own keyring before installing it.
CONTAINER_SCRIPT = r"""
set -euo pipefail
mkdir /work
tar -C /work -xf -
sqv --keyring=/usr/share/keyrings/debian-archive-keyring.pgp --cleartext \
    --output=/tmp/Release /wheels/apt/InRelease
python - <<'PY'
import hashlib, lzma, re
release = open("/tmp/Release").read()
packages = open("/wheels/apt/Packages.xz", "rb").read()
digest = hashlib.sha256(packages).hexdigest()
listed = rf"^ {digest} +[0-9]+ main/binary-amd64/Packages.xz$"
assert re.search(listed, release, re.M), "Packages.xz is not in the signed Release"
stanza = next(
    s for s in lzma.decompress(packages).decode().split("\n\n")
    if s.startswith("Package: libexpat1\n")
)
expected = re.search(r"^SHA256: ([0-9a-f]{64})$", stanza, re.M).group(1)
deb = open("/wheels/apt/libexpat1.deb", "rb").read()
assert hashlib.sha256(deb).hexdigest() == expected, "libexpat1.deb hash mismatch"
PY
dpkg -i /wheels/apt/libexpat1.deb > /dev/null
python -m venv /venv
/venv/bin/pip install -q --no-index --find-links /wheels/run --require-hashes \
    -r /wheels/requirements.txt
/venv/bin/pip install -q --no-index --find-links /wheels/build setuptools
/venv/bin/pip install -q --no-index --no-deps --no-build-isolation \
    -e /work/packages/field-shift-contracts -e /work/packages/field-shift-science \
    -e /work/apps/api
cd /work/apps/api
/venv/bin/ruff check .
/venv/bin/ruff format --check .
/venv/bin/python -m pytest -q -m "not integration" -p no:cacheprovider
cd /work/.agents/skills/issue-worktree/scripts
/venv/bin/python -m unittest -q test_integrate.LockTests
"""


def download(requirements, destination, *extra):
    platforms = [arg for name in PLATFORMS for arg in ("--platform", name)]
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:"]
        + [*platforms, "--python-version", "3.12", "--implementation", "cp"]
        + ["-d", str(destination), *requirements, *extra],
        check=True,
    )


def image_python_version():
    env = subprocess.run(
        ["docker", "image", "inspect", IMAGE, "--format", "{{json .Config.Env}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return re.search(r'"PYTHON_VERSION=([0-9.]+)"', env).group(1)


def image_codename():
    return subprocess.run(
        ["docker", "run", "--rm", "--pull", "never", "--network", "none", IMAGE]
        + ["sh", "-c", ". /etc/os-release && echo $VERSION_CODENAME"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def fetch(url, destination):
    with urlopen(url, timeout=60) as response:
        destination.write_bytes(response.read())
    return destination.read_bytes()


def debian_package(directory, codename, name="libexpat1"):
    """Download a package with the index that the container verifies before use."""
    directory.mkdir(exist_ok=True)
    fetch(f"{DEBIAN}/dists/{codename}/InRelease", directory / "InRelease")
    index = fetch(
        f"{DEBIAN}/dists/{codename}/main/binary-amd64/Packages.xz",
        directory / "Packages.xz",
    )
    stanza = next(
        s
        for s in lzma.decompress(index).decode().split("\n\n")
        if s.startswith(f"Package: {name}\n")
    )
    filename = re.search(r"^Filename: (\S+)$", stanza, re.M).group(1)
    fetch(f"{DEBIAN}/{filename}", directory / f"{name}.deb")


def for_container(exported, python_version):
    """Keep the hashed pins whose markers match the container, without the markers.

    pip download would evaluate markers for the host; the container's pip still applies
    the original markers when it installs from the full requirements file.
    """
    from packaging.markers import Marker

    environment = {
        "implementation_name": "cpython",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_python_implementation": "CPython",
        "platform_system": "Linux",
        "python_full_version": python_version,
        "python_version": ".".join(python_version.split(".")[:2]),
        "sys_platform": "linux",
    }
    kept = []
    for entry in re.split(r"\n(?=\S)", exported):
        if not entry.strip() or entry.startswith("#"):
            continue
        head, _, rest = entry.partition(" \\\n")
        pin, _, marker = head.partition(" ; ")
        if not marker or Marker(marker).evaluate(environment):
            kept.append(f"{pin} \\\n{rest}" if rest else pin)
    return "\n".join(kept) + "\n"


def git(checkout, *argv):
    return subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            *argv,
        ],
        check=True,
        capture_output=True,
    ).stdout


def wheelhouse(checkout, tree, cache):
    python_version, codename = image_python_version(), image_codename()
    lock = git(checkout, "cat-file", "blob", f"{tree}:apps/api/uv.lock")
    seed = lock + CONTAINER_SCRIPT.encode() + (python_version + codename).encode()
    target = cache / hashlib.sha256(seed).hexdigest()[:16]
    if (target / "complete").exists():
        return target
    target.mkdir(parents=True, exist_ok=True)
    exported = subprocess.run(
        ["uv", "export", "--project", str(checkout / "apps/api"), "--locked"]
        + ["--extra", "dev", "--no-emit-project", "--no-emit-local", "-q"]
        + ["--format", "requirements-txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    (target / "requirements.txt").write_text(exported)
    selected = target / "download-requirements.txt"
    selected.write_text(for_container(exported, python_version))
    download(["--no-deps", "-r", str(selected)], target / "run")
    download(["setuptools>=75"], target / "build")
    debian_package(target / "apt", codename)
    (target / "complete").write_text(python_version + "\n")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path("."))
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache" / "field-shift-linux-check"
    )
    args = parser.parse_args()
    checkout = args.checkout.resolve(strict=True)
    cache = args.cache.resolve()
    if cache == checkout or checkout in cache.parents:
        parser.error("The wheel cache must be outside the checkout")
    tree = git(checkout, "write-tree").decode().strip()
    wheels = wheelhouse(checkout, tree, cache)
    command = ["docker", "run", "--rm", "-i", "--pull", "never", "--network", "none"]
    command += ["--cpus", "2", "--memory", "4g", "--pids-limit", "512"]
    command += ["--label", "field-shift.purpose=linux-check"]
    command += ["--mount", f"type=bind,source={wheels},target=/wheels,readonly"]
    command += [IMAGE, "bash", "-c", CONTAINER_SCRIPT]
    print(f"Linux check: staged tree {tree}, {IMAGE}, no network", flush=True)
    archive = subprocess.Popen(
        ["git", "-C", str(checkout), "-c", "core.autocrlf=false"]
        + ["-c", "core.eol=lf", "archive", tree],
        stdout=subprocess.PIPE,
    )
    result = subprocess.run(command, stdin=archive.stdout, check=False)
    archive.stdout.close()
    if archive.wait():
        raise SystemExit("git archive of the staged tree failed")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
