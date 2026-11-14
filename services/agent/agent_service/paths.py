"""Writable runtime locations, independent of installed code and working directory."""

import os
from collections.abc import Mapping
from pathlib import Path


def data_directory(environ: Mapping[str, str] | None = None) -> Path:
    """Use an explicit data directory or the user's standard application data directory."""
    env = os.environ if environ is None else environ
    explicit = env.get("AGENT_DATA_DIR")
    if explicit:
        path = Path(explicit).expanduser()
    elif os.name == "nt":
        path = Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        path /= "field-shift-agent"
    else:
        path = Path(env.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
        path /= "field-shift-agent"
    if not path.is_absolute():
        raise ValueError("AGENT_DATA_DIR and application data directory overrides must be absolute")
    return path


def knowledge_directory(environ: Mapping[str, str] | None = None) -> Path:
    return data_directory(environ) / "knowledge"


def knowledge_index_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = env.get("AGENT_RAG_INDEX_PATH")
    return (
        Path(explicit).expanduser()
        if explicit
        else knowledge_directory(env) / "index" / "knowledge.sqlite"
    )


def reports_directory(environ: Mapping[str, str] | None = None) -> Path:
    return data_directory(environ) / "evals" / "reports"
