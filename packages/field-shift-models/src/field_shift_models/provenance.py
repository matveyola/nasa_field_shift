"""Content addressing of exact installed distributions and canonical inputs."""

import hashlib
import json
from importlib.metadata import distribution


def _json(value):
    from datetime import date, datetime

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Cannot canonicalize {type(value)}")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json
        ).encode()
    ).hexdigest()


def distribution_manifest(name):
    dist = distribution(name)
    files = {}
    for path in sorted(dist.files, key=str):
        # Hash code, bundled data, and legal terms; exclude mutable bytecode/installer metadata.
        if str(path).endswith((".py", ".json", ".yaml", ".sql", ".txt", ".csv")) or (
            "license" in str(path).lower()
        ):
            files[str(path)] = hashlib.sha256(dist.locate_file(path).read_bytes()).hexdigest()
    licences = {k: v for k, v in files.items() if "license" in k.lower()}
    return {
        "name": name,
        "version": dist.version,
        "files_sha256": digest(files),
        "licences": licences,
        "files": files,
    }
