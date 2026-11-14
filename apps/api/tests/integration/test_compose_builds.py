"""Validate build topology without contacting the Docker daemon or live providers."""

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
API_DIR = Path(__file__).resolve().parents[2]
ROOT = API_DIR.parent.parent


def test_compose_build_targets_do_not_publish_the_same_image():
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required to inspect the build plan.")
    command = [
        "docker",
        "compose",
        "--env-file",
        ".env.example",
        "--profile",
        "agent",
        "build",
        "--print",
    ]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
    )
    plan = json.loads(result.stdout)
    targets = plan["target"]
    assert targets, "The default stack must define buildable application images."
    tags = Counter(tag for target in targets.values() for tag in target.get("tags", []))
    duplicates = sorted(tag for tag, count in tags.items() if count > 1)
    assert not duplicates, f"Concurrent build targets publish the same image: {duplicates}"
