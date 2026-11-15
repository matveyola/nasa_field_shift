"""Installed imports, immutable resources and writable outputs must not depend on cwd."""

import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from agent_service.config import Settings
from agent_service.paths import data_directory, knowledge_index_path, reports_directory


def isolated_python(tmp_path, *arguments, data=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_")}
    env.pop("PYTHONPATH", None)
    env["AGENT_DATA_DIR"] = str(data or tmp_path / "state")
    return subprocess.run(
        [sys.executable, "-I", *arguments],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_installed_imports_and_fixture_readiness_outside_checkout(tmp_path):
    result = isolated_python(
        tmp_path,
        "-c",
        """
import json
from importlib.resources import files
from fastapi.testclient import TestClient
from agent_service.api import create_app
from agent_service.config import Settings
from agent_service.integrations.fixture import FixtureGeoProvider
from agent_service.integrations.contract_check import examples
from contracts.export import SCHEMA_PATH, build
from evals.harness import build_request
from evals.cases import CASES
from integration_stub.app import create_app as create_stub
assert SCHEMA_PATH.read_text(encoding="utf-8") == build()
assert len(FixtureGeoProvider().contexts) == 4
assert "field-profile" in examples()
assert build_request(CASES[0]).field_context.field_id
assert create_stub()
assert json.loads(files("evals.retrieval").joinpath("benchmark.json").read_text())["cases"]
settings = Settings(
    agent="scripted-llm", rag_mode="off", geo_provider="fixture", sim_provider="fixture"
)
with TestClient(create_app(settings)) as client:
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    print(response.json()["status"])
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ready"
    assert not (tmp_path / "contracts").exists()
    assert not (tmp_path / "state").exists()


def test_schema_export_requires_explicit_output_and_preserves_package(tmp_path):
    resource = files("contracts").joinpath("schema", "fieldshift-contracts.schema.json")
    original = resource.read_bytes()
    rejected = isolated_python(tmp_path, "-m", "contracts.export")
    assert rejected.returncode == 2
    assert "writing requires --output-dir" in rejected.stderr
    checked = isolated_python(tmp_path, "-m", "contracts.export", "--check")
    assert checked.returncode == 0, checked.stderr
    destination = tmp_path / "generated"
    exported = isolated_python(tmp_path, "-m", "contracts.export", "--output-dir", str(destination))
    assert exported.returncode == 0, exported.stderr
    assert (destination / resource.name).read_bytes() == original
    assert resource.read_bytes() == original
    invalid = json.loads((destination / resource.name).read_text())
    invalid["title"] = "stale"
    (destination / resource.name).write_text(json.dumps(invalid))
    stale = isolated_python(
        tmp_path, "-m", "contracts.export", "--check", "--output-dir", str(destination)
    )
    assert stale.returncode == 1


def test_default_data_location_survives_working_directory_change(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_DATA_DIR", raising=False)
    monkeypatch.delenv("AGENT_RAG_INDEX_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    before = data_directory()
    monkeypatch.chdir(tmp_path)
    assert data_directory() == before == tmp_path / "user-data" / "field-shift-agent"
    assert knowledge_index_path() == before / "knowledge" / "index" / "knowledge.sqlite"
    assert reports_directory() == before / "evals" / "reports"
    assert Settings().rag_index_path == str(knowledge_index_path())
    assert not before.exists()


def test_explicit_data_and_index_configuration_is_shared_with_settings(tmp_path):
    env = {"AGENT_DATA_DIR": str(tmp_path / "state")}
    assert Settings.from_env(env).rag_index_path == str(knowledge_index_path(env))
    env["AGENT_RAG_INDEX_PATH"] = str(tmp_path / "mounted" / "knowledge.sqlite")
    assert Settings.from_env(env).rag_index_path == env["AGENT_RAG_INDEX_PATH"]
    assert knowledge_index_path(env) == tmp_path / "mounted" / "knowledge.sqlite"


def test_relative_data_override_is_rejected():
    with pytest.raises(ValueError, match="must be absolute"):
        data_directory({"AGENT_DATA_DIR": "./data"})


def test_home_index_override_is_expanded_consistently(tmp_path, monkeypatch):
    env = {"AGENT_RAG_INDEX_PATH": "~/knowledge.sqlite"}
    expected = Path.home() / "knowledge.sqlite"
    monkeypatch.chdir(tmp_path)
    assert knowledge_index_path(env) == expected
    assert Settings.from_env(env).rag_index_path == str(expected)


@pytest.mark.parametrize("value", ["knowledge.sqlite", "./data/index.sqlite", "../index.sqlite"])
def test_relative_index_override_is_rejected(value):
    env = {"AGENT_RAG_INDEX_PATH": value}
    with pytest.raises(ValueError, match="AGENT_RAG_INDEX_PATH must be absolute"):
        knowledge_index_path(env)
    with pytest.raises(ValueError, match="AGENT_RAG_INDEX_PATH must be absolute"):
        Settings.from_env(env)


def test_eval_outputs_use_configured_writable_state(tmp_path):
    result = isolated_python(
        tmp_path,
        "-c",
        """
from agent_service.paths import reports_directory
from evals import run, acceptance
from evals.retrieval import run as retrieval
assert run.REPORTS == acceptance.REPORTS == retrieval.REPORTS == reports_directory()
print(reports_directory())
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(tmp_path / "state" / "evals" / "reports")
