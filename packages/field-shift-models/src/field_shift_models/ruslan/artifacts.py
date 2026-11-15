"""Bounded, byte-exact loading of the private team's original research artifacts."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

PROVENANCE_SHA256 = "0ccc0320430599c85560849df53411dd47f28e41cac58fcdd5fbc59501eac2d6"
MODEL_SHA256 = "024c35a39470c964c6863af8d73e624ee869d211e5a10f6b954d5d2122dbe914"
FEATURES = (
    "z_e0",
    "z_e1",
    "z_e0_mean3",
    "z_e0_mean7",
    "e0_drydown_sd",
    "z_l4",
    "z_l4_mean3",
    "z_l4rz",
    "z_l3_latest",
    "l3_age_days",
    "season_sin",
    "season_cos",
    "stemp",
    "tair",
    "sand",
    "clay",
    "om",
)
MAX_ASSET_BYTES = 2 * 1024 * 1024


class ArtifactIntegrityError(ValueError):
    """Original artifact bytes or their receipt are missing or have changed."""


def _read(root, name, limit=MAX_ASSET_BYTES):
    try:
        with root.joinpath(name).open("rb") as source:
            data = source.read(limit + 1)
    except OSError as exc:
        raise ArtifactIntegrityError(f"Missing research asset: {name}") from exc
    if len(data) > limit:
        raise ArtifactIntegrityError(f"Research asset exceeds byte bound: {name}")
    return data


def load_artifacts(directory: Path | None = None):
    if directory is not None and not directory.is_absolute():
        raise ArtifactIntegrityError("Explicit artifact directory must be absolute")
    root = directory if directory is not None else files(__package__).joinpath("assets")
    raw = _read(root, "provenance.json", 64 * 1024)
    if hashlib.sha256(raw).hexdigest() != PROVENANCE_SHA256:
        raise ArtifactIntegrityError("Research provenance receipt checksum mismatch")
    receipt = json.loads(raw)
    payload = {}
    for name in ("gbm.txt", "q10.txt", "q90.txt", "meta.json"):
        raw = _read(root, name)
        expected = receipt["artifacts"][name]
        if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ArtifactIntegrityError(f"Research artifact checksum mismatch: {name}")
        payload[name] = raw
    if (
        hashlib.sha256(b"".join(payload[n] for n in ("gbm.txt", "q10.txt", "q90.txt"))).hexdigest()
        != MODEL_SHA256
    ):
        raise ArtifactIntegrityError("Combined model checksum mismatch")
    meta = json.loads(payload["meta.json"])
    if tuple(meta["features"]) != FEATURES or meta["candidate"] != "anomaly_lgbm_era5_level":
        raise ArtifactIntegrityError("Research feature schema mismatch")
    return receipt, meta, {k: payload[f"{k}.txt"].decode() for k in ("gbm", "q10", "q90")}
