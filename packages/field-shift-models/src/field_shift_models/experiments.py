"""Optional, budget-controlled comparison of externally frozen EO features.

No torch/GPU/model downloads in normal inference or CI. Extraction must be supplied
as a separately permissioned immutable artifact before this experiment can run.
"""

from dataclasses import replace

from .learning import residual_benchmark
from .provenance import digest


def compare_frozen_embeddings(rows, embeddings, manifest, gate, *, holdout_opened_at):
    if not rows or not embeddings:
        return {
            "training_status": "insufficient_training_data",
            "metrics": None,
            "experiment": "not_run",
            "reason": "Independent crop/stress labels and frozen "
            "embeddings unavailable; no pretrained accuracy claim",
        }
    required = {
        "checkpoint",
        "checkpoint_sha256",
        "extractor_version",
        "bands",
        "reflectance_scale",
        "temporal_policy",
        "geolocation_policy",
        "pooling",
        "label_support",
        "permission",
        "features_sha256",
        "available_at_by_observation",
    }
    if not required <= manifest.keys():
        raise ValueError("Incomplete frozen feature provenance")
    if (
        manifest["checkpoint"]
        not in (
            "ibm-nasa-geospatial/Prithvi-EO-2.0-tiny-TL",
            "ibm-nasa-geospatial/Prithvi-EO-2.0-100M-TL",
        )
        or len(manifest["checkpoint_sha256"]) != 64
    ):
        raise ValueError("Pinned tiny/100M checkpoint required")
    if manifest["bands"] != ["blue", "green", "red", "nir_narrow", "swir1", "swir2"] or (
        manifest["reflectance_scale"] != "surface_reflectance_0_1"
    ):
        raise ValueError("Pin HLS-compatible six bands and physical reflectance conversion")
    if manifest["label_support"] != "field_aggregate_not_pixel_ground_truth":
        raise ValueError("Field labels cannot be presented as pixel ground truth")
    if manifest["features_sha256"] != digest(embeddings):
        raise ValueError("Frozen embedding checksum mismatch")
    from datetime import datetime

    embedded = []
    for row in rows:
        key = row.observation.observation_id
        acquired = datetime.fromisoformat(manifest["available_at_by_observation"][key])
        if acquired.utcoffset() is None or acquired > row.issue_at:
            raise ValueError("EO features unavailable at issue time")
        embedded.append(
            replace(
                row,
                features=tuple(embeddings[key]),
                predictor_available_at=max(row.predictor_available_at, acquired),
            )
        )
    # Ordinary features include caller-pinned NDVI; identical partitions/targets/gates.
    ordinary = residual_benchmark(rows, gate, holdout_opened_at=holdout_opened_at)
    frozen = residual_benchmark(embedded, gate, holdout_opened_at=holdout_opened_at)
    return {
        "experiment": "frozen_features_small_heads",
        "ordinary_ndvi_baseline": ordinary,
        "prithvi_frozen": frozen,
        "manifest_sha256": digest(manifest),
        "fine_tuning": False,
        "serving_changed": False,
    }
