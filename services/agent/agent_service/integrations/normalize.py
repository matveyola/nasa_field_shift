"""The ingestion boundary: external geo and simulation results are checked here before the agent uses them.

Schema validation (types, units from the M0 allowlist, finite numbers, known data_status values,
provenance requirements) already happened when the response was parsed into the contract
models. This module adds what a schema cannot express: identity (field, request, revision),
geometry consistency, demo/live labelling, dataset-specific scientific rules, expiry, and
units per metric. Nothing is converted: a value in an unexpected unit is dropped, not rescaled.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from contracts.integration import ComparisonRequest, ComparisonResponse, FieldProfileResponse
from contracts.models import Fact, FieldContext, MapLayer, Provenance, Quantity, Score, TimeseriesInfo

from .ports import IntegrationFailure

CLOCK_SKEW = timedelta(minutes=5)
CENTROID_TOLERANCE_DEG = 0.02          # ~2 km: same field, different digitisation
SMAP_L4_MIN_RESOLUTION_M = 9_000       # SPL4SMGP native grid (EASE-Grid 2.0, 9 km)
POWER_MIN_RESOLUTION_M = 50_000        # NASA POWER meteorology is a 0.5 x 0.625 degree grid

# Label families whose unit is fixed: anything else is dropped (no silent conversion).
EXPECTED_UNITS: dict[str, set[str]] = {
    "soil_moisture": {"m3/m3"},
    "precipitation": {"mm"},
    "temperature": {"Cel"},
    "soil_ph": {"[pH]"},
    "ndvi": {"1"},
    "reference_et0": {"mm", "mm/d"},
    "growing_degree_days": {"Cel.d"},
}
SCORE_UNITS = {"water_risk": "%", "soil_cover_days": "d", "n_input": "kg/ha", "yield_risk": "%", "yield_loss_water": "%"}


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ProfileCheck:
    facts: list[Fact]
    layers: list[MapLayer]
    timeseries: list[TimeseriesInfo]
    dropped: list[tuple[str, str]] = field(default_factory=list)   # (id, reason)
    warnings: list[str] = field(default_factory=list)


def _provenance_problem(p: Provenance, now: datetime) -> str | None:
    if p.retrieved_at > now + CLOCK_SKEW:
        return "retrieved_at is in the future"
    product = f"{p.source.provider} {p.source.product_id}".lower() if p.source else ""
    if "spl4sm" in product:
        if p.kind != "satellite_product":
            return "SMAP L4 must be described as the published model-assimilation product, not as an estimate of ours"
        if p.spatial and p.spatial.resolution_m < SMAP_L4_MIN_RESOLUTION_M:
            return "SMAP L4 claims a finer resolution than its 9 km grid (downscaled values are a separate ml_estimate)"
    if "power" in product and p.spatial and p.spatial.resolution_m < POWER_MIN_RESOLUTION_M:
        return "NASA POWER claims a finer resolution than its 0.5 degree grid"
    if "soilgrids" in product and "nasa" in product:
        return "SoilGrids is an ISRIC product, not NASA"
    return None


def _unit_problem(label: str, q: Quantity | None) -> str | None:
    if q is None:
        return None
    for family, units in EXPECTED_UNITS.items():
        if family in label and q.unit not in units:
            return f"unit {q.unit} is not a {family} unit ({', '.join(sorted(units))}); not converted"
    return None


def _check_location(resp: FieldProfileResponse, ctx: FieldContext) -> None:
    lon, lat = resp.centroid
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise IntegrationFailure("invalid_response", "centroid outside WGS84 bounds")
    clon, clat = ctx.centroid
    if math.hypot(lon - clon, lat - clat) <= CENTROID_TOLERANCE_DEG:
        return
    if math.hypot(lat - clon, lon - clat) <= CENTROID_TOLERANCE_DEG:
        raise IntegrationFailure("invalid_response", "centroid is [lat, lon]; the contract requires [lon, lat]")
    raise IntegrationFailure("wrong_field", "the service's geometry for this field does not match the host's field")


def check_profile(resp: FieldProfileResponse, ctx: FieldContext, request_id: str, now: datetime | None = None) -> ProfileCheck:
    """Validate a field profile against the host's FieldContext. Raises for whole-response problems,
    drops (and reports) individual facts that break a rule, keeps everything else verbatim."""
    now = now or _now()
    if resp.service.service != "geo":
        raise IntegrationFailure("invalid_response", f"a {resp.service.service} service answered a geo request")
    if resp.request_id != request_id:
        raise IntegrationFailure("invalid_response", "response is for another request")
    if resp.profile.field_id != ctx.field_id:
        raise IntegrationFailure("wrong_field", "profile is for another field")
    if resp.generated_at > now + CLOCK_SKEW:
        raise IntegrationFailure("invalid_response", "generated_at is in the future")
    _check_location(resp, ctx)

    provenances = [f.provenance for f in resp.profile.facts] + [la.provenance for la in resp.profile.available_layers] \
        + [t.provenance for t in resp.profile.timeseries_index]
    if resp.service.mode != "live" and any(p.data_status in ("live", "cached") for p in provenances):
        raise IntegrationFailure("invalid_response", f"a {resp.service.mode} service labelled data as real (live/cached)")

    out = ProfileCheck(facts=[], layers=[], timeseries=[])
    for f in resp.profile.facts:
        problem = _unit_problem(f.label_key, f.quantity) or _provenance_problem(f.provenance, now)
        if problem:
            out.dropped.append((f.id, problem))
        else:
            out.facts.append(f)
    for la in resp.profile.available_layers:
        if problem := _provenance_problem(la.provenance, now):
            out.dropped.append((la.layer_id, problem))
        else:
            out.layers.append(la)
    for t in resp.profile.timeseries_index:
        if problem := _provenance_problem(t.provenance, now):
            out.dropped.append((t.metric_id, problem))
        else:
            out.timeseries.append(t)
    if resp.status == "partial":
        out.warnings.append("partial profile: some datasets were not available")
    if any(p.source and p.source.version == "unverified" for p in provenances):
        out.warnings.append("some product versions are unverified")
    out.warnings += [w.detail or w.code for w in resp.warnings]
    return out


# ------------------------------------------------------------------ simulation


@dataclass(frozen=True)
class ComparisonRecord:
    """What the agent may reference later in the turn (UI actions) — never the numbers themselves."""

    result_id: str
    field_id: str
    field_context_revision: int
    scenario: str
    rotation_ids: tuple[str, ...]
    expires_at: datetime
    mode: str


@dataclass
class ComparisonCheck:
    response: ComparisonResponse
    record: ComparisonRecord | None
    facts: dict[str, Fact]                      # fact id -> Fact, one per score (+ the result's own facts)
    score_refs: dict[str, dict[str, str]]       # rotation_id -> metric -> fact id
    warnings: list[str] = field(default_factory=list)


def idempotency_key(request_json: dict, scope: str) -> str:
    """Stable key for one logical comparison request (same field, revision, scenario, constraints, rotations) from
    one caller (`scope`, the session). Retries by the same caller de-duplicate; other callers never share a key."""
    canon = json.dumps({"scope": scope, "request": request_json}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "idem_" + hashlib.sha256(canon.encode()).hexdigest()[:32]


def short_id(result_id: str) -> str:
    return hashlib.sha256(result_id.encode()).hexdigest()[:10]


def check_comparison(resp: ComparisonResponse, sent: ComparisonRequest, now: datetime | None = None) -> ComparisonCheck:
    now = now or _now()
    want = sent.comparison
    if resp.service.service != "simulation":
        raise IntegrationFailure("invalid_response", f"a {resp.service.service} service answered a simulation request")
    if resp.request_id != sent.request_id:
        raise IntegrationFailure("invalid_response", "response is for another request")
    if resp.field_id != want.field_id:
        raise IntegrationFailure("wrong_field", "result is for another field")
    if resp.field_context_revision != want.field_context_revision:
        raise IntegrationFailure("wrong_revision", "result is for another context revision")
    if resp.scenario != want.scenario:
        raise IntegrationFailure("invalid_response", "result is for another scenario")
    if resp.expires_at <= now:
        raise IntegrationFailure("expired", "result has already expired")
    if resp.created_at > now + CLOCK_SKEW or (resp.computed_at and resp.computed_at > now + CLOCK_SKEW):
        raise IntegrationFailure("invalid_response", "timestamps are in the future")
    check = ComparisonCheck(response=resp, record=None, facts={}, score_refs={})
    if resp.status != "completed":
        return check
    comparison = resp.comparison
    assert comparison is not None  # guaranteed by the contract model
    if want.rotation_ids and (extra := {r.rotation_id for r in comparison.rotations} - set(want.rotation_ids)):
        raise IntegrationFailure("invalid_response", f"result contains rotations that were not requested: {sorted(extra)}")

    scores: list[tuple[str, str, Score]] = [
        (r.rotation_id, metric, score) for r in comparison.rotations
        for metric, score in r.scores if score is not None
    ]
    provenances = [s.provenance for _, _, s in scores] + [f.provenance for f in comparison.facts]
    if resp.service.mode != "live" and any(p.data_status in ("live", "cached") for p in provenances):
        raise IntegrationFailure("invalid_response", f"a {resp.service.mode} simulation labelled results as real")
    if resp.service.mode == "live" and any(p.data_status == "fixture" for p in provenances):
        check.warnings.append("the live service returned values labelled as fixture data")
    for rotation_id, metric, score in scores:
        if SCORE_UNITS.get(metric) and score.unit != SCORE_UNITS[metric]:
            raise IntegrationFailure("invalid_response", f"{metric} must be in {SCORE_UNITS[metric]}, got {score.unit}")
        if score.provenance.kind == "ml_estimate" and not (score.provenance.model and score.provenance.model.validation):
            check.warnings.append(f"{metric} comes from a model without reported validation")

    # Every score becomes a fact the model can only cite as a token. Provenance is kept verbatim;
    # the method text gains the result context (scenario, result id, engine) so it is never lost.
    sid = short_id(resp.result_id)
    context = f" | simulation output; scenario={resp.scenario}; result={resp.result_id}; engine={resp.engine.name} {resp.engine.version}"
    for rotation_id, metric, score in scores:
        fid = f"sim.{sid}.{rotation_id}.{metric}"[:128]
        method = ((score.provenance.method or "simulation") + context)[:300]
        fact = Fact(
            id=fid, domain="rotation", label_key=f"fact.rotation_{metric}",
            quantity=Quantity(value=score.value, unit=score.unit, uncertainty=score.uncertainty),
            provenance=score.provenance.model_copy(update={"method": method}),
        )
        check.facts[fid] = Fact.model_validate_json(fact.model_dump_json())  # re-validate in wire form
        check.score_refs.setdefault(rotation_id, {})[metric] = fid
    for f in comparison.facts:
        fid = f"sim.{sid}.{f.id}"[:128]
        check.facts[fid] = Fact.model_validate_json(f.model_copy(update={"id": fid}).model_dump_json())
    check.record = ComparisonRecord(
        result_id=resp.result_id, field_id=resp.field_id, field_context_revision=resp.field_context_revision,
        scenario=resp.scenario, rotation_ids=tuple(r.rotation_id for r in comparison.rotations),
        expires_at=resp.expires_at, mode=resp.service.mode,
    )
    return check
