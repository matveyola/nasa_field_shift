"""Bounded experimental handoff operations with explicit acquisition and replay."""

import hashlib
from datetime import UTC, date, datetime
from importlib.resources import files

from .interpretation import climate_diagnostics, collect_forecast_diagnostics, interpretation
from .modules.rotations import (
    assessment,
    climate_balance,
    climate_planner,
    hybrid_forecast,
    supplied_balance,
)
from .modules.rotations.equipment import EQUIPMENT
from .modules.rotations.historical_fields import load_fields
from .modules.rotations.hybrid_profiles import crop_catalog, strategy_hybrids
from .modules.rotations.rotation_agronomy import cultivar_references
from .network import canonical, evidence_session
from .replay_sutherland_2026_28 import run_sutherland_2027

SOURCE_COMMIT = "a68b67c70a470bf8a5ba8f9f8e59350f029200fc"


def installed_identity():
    root = files("field_shift_rotation_model")
    entries = []

    def walk(directory, prefix=""):
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            name = prefix + entry.name
            if entry.is_dir() and entry.name != "__pycache__":
                walk(entry, name + "/")
            elif entry.is_file() and entry.name.endswith((".py", ".json", ".gz", ".md")):
                entries.append((name, hashlib.sha256(entry.read_bytes()).hexdigest()))

    walk(root)
    return "rotation-handoff-a68b67c-installed-v1:" + hashlib.sha256(canonical(entries)).hexdigest()


ENGINE_VERSION = installed_identity()
OPERATIONS = (
    "forecast",
    "climate_step",
    "climate_compare",
    "assessment",
    "compare_water_balances",
    "sutherland_2027",
)
KINDS = dict(
    zip(
        OPERATIONS,
        (
            "hybrid_scenario",
            "conditional_climate",
            "conditional_climate",
            "historical_replay",
            "supplied_balance",
            "conditional_climate",
        ),
        strict=True,
    )
)
MAPPING = {
    "maize_grain": "Maize",
    "soybeans": "Soy",
    "sunflower": "Sunflower",
    "barley": "Barley",
    "rapeseed_canola": "Rapeseed",
    "peas_dry": "Peas",
    "wheat_spring": "Wheat",
    "wheat_winter_frozen": "Wheat",
    "wheat_winter_nonfrozen": "Wheat",
}
SCHEMAS = {
    "forecast": hybrid_forecast.ForecastRequest,
    "climate_step": climate_planner.ClimateStepRequest,
    "climate_compare": climate_planner.ClimatePlansRequest,
    "assessment": assessment.AssessmentRequest,
    "compare_water_balances": supplied_balance.CompareSuppliedBalance,
}
LIMITATIONS = [
    (
        "Experimental scenario calculations; field yield, profit and "
        "decision accuracy are not validated."
    ),
    (
        "Climate defaults include 50% available opening water and 10% "
        "daily gravity drainage; these are assumptions, not field "
        "measurements."
    ),
    (
        "Sutherland historical/NASA weights of 0.5 are planning settings, "
        "not calibrated probabilities."
    ),
    (
        "Climate dates are restricted to 2040–2049, northern "
        "spring/summer crops, March–July and at most three decisions "
        "spanning five years."
    ),
    (
        "Forecast mode combines current public weather with historical "
        "analogs; it is not a calibrated multiyear forecast."
    ),
    (
        "Reference yield times price is gross revenue; revenue after seed "
        "excludes all other farm costs."
    ),
    (
        "Thirteen profile execution capabilities and seven climate crops "
        "do not extend the existing allocation catalog or production "
        "yield validation gates."
    ),
    (
        "Bundled and newly acquired historical vintages are not evidence "
        "of availability at an earlier decision date."
    ),
]


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def catalog():
    return {
        "status": "available",
        "engine_version": ENGINE_VERSION,
        "source_commit": SOURCE_COMMIT,
        "empirical_status": "not_validated",
        "operations": list(OPERATIONS),
        "crops": [
            {
                "id": c.id,
                "name": c.name,
                "climate_supported": c.id in climate_balance.SUPPORTED_CROPS,
                "strategy_supported": c.id in climate_balance.STRATEGY_CROPS,
                "allocation_crop_id": MAPPING.get(c.id),
                "profile": c.model_dump(mode="json"),
            }
            for c in crop_catalog()
        ],
        "request_schemas": {k: v.model_json_schema() for k, v in SCHEMAS.items()},
        "strategy_hybrids": [
            {
                "choice_id": key,
                "crop_id": row["crop_id"],
                "name": row["name"],
                "source_url": row["source"],
            }
            for key, row in strategy_hybrids().items()
        ],
        "equipment": [row.model_dump(mode="json") for row in EQUIPMENT],
        "cultivar_references": [row.model_dump(mode="json") for row in cultivar_references()],
        "limitations": LIMITATIONS,
        "reasons": [],
    }


def calculate(operation, parameters, decision_date):
    if operation not in OPERATIONS:
        raise ValueError("Unsupported operation")
    model = SCHEMAS.get(operation)
    request = model.model_validate(parameters) if model else None
    if operation == "forecast":
        field = request.field or load_fields().get(request.field_id)
        if field is None:
            raise ValueError("Unknown reference field")
        result = hybrid_forecast.forecast(request, field, as_of=decision_date)
    elif operation == "climate_step":
        result = climate_planner.ClimateStepResult.model_validate(climate_planner.step(request))
    elif operation == "climate_compare":
        result = climate_planner.ClimatePlansResult.model_validate(climate_planner.compare(request))
    elif operation == "assessment":
        field = load_fields().get(request.field_id)
        if field is None:
            raise ValueError("Unknown reference field")
        result = assessment.assess(request, field)
    elif operation == "compare_water_balances":
        result = supplied_balance.compare(request)
    else:
        if parameters:
            raise ValueError("Sutherland reference scenario accepts no parameters")
        result = run_sutherland_2027(climate_balance.engine_directory())
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else result


def previous_records(document, decision_date):
    previous = document.get("previous_evidence")
    parameters = document["parameters"]
    if (
        not isinstance(previous, dict)
        or document["operation"] != "climate_step"
        or parameters.get("strategy_mode") not in {"sequence", "compare"}
        or previous.get("operation") != "climate_step"
        or previous.get("decision_date") != decision_date
        or previous.get("source_commit") != SOURCE_COMMIT
    ):
        return None
    old_parameters = previous.get("parameters", {})
    old_planting = old_parameters.get("planting_date", "")
    current_planting = parameters.get("planting_date", "")
    selected = parameters.get("selected") or []
    if not old_planting or (
        old_planting[:4] != current_planting[:4]
        and (not selected or old_planting != selected[-1].get("planting_date"))
    ):
        return None
    if (
        old_parameters.get("strategy_mode") not in {"sequence", "compare"}
        or old_parameters.get("field") != parameters.get("field")
        or digest({k: v for k, v in previous.items() if k != "evidence_sha256"})
        != previous.get("evidence_sha256")
    ):
        return None
    # Requests are reused only for an identical URL, method and body. Captured bytes
    # retain their original timestamps and are validated by the evidence session.
    return previous.get("records")


def acquire(document):
    # Current acquisition only: never present today's revised inputs as a vintage forecast.
    today = datetime.now(UTC).date()
    if document["decision_date"] != today.isoformat():
        raise ValueError("New public acquisition requires the actual current UTC decision date")
    with evidence_session(
        acquire=document.get("allow_public_data", False),
        records=previous_records(document, today.isoformat()),
    ) as session:
        with collect_forecast_diagnostics() as diagnostics:
            preview = calculate(document["operation"], document["parameters"], today)
    if document["operation"] in {"climate_step", "climate_compare", "sutherland_2027"}:
        diagnostics.extend(climate_diagnostics(preview))
    snapshot = {
        "operation": document["operation"],
        "parameters": document["parameters"],
        "decision_date": today.isoformat(),
        "engine_version": ENGINE_VERSION,
        "source_commit": SOURCE_COMMIT,
        "acquired_at": datetime.now(UTC).isoformat(),
        "records": session.records,
        "calculation_sha256": digest(preview),
        "land_status": document.get("land_status", "unknown"),
        "interpretation": interpretation(document.get("land_status", "unknown"), diagnostics),
    }
    snapshot["input_sha256"] = digest(
        {
            "operation": snapshot["operation"],
            "parameters": snapshot["parameters"],
            "decision_date": snapshot["decision_date"],
        }
    )
    snapshot["evidence_sha256"] = digest(snapshot)
    return snapshot


def evaluate(snapshot):
    evidence_hash = snapshot["evidence_sha256"]
    if digest({k: v for k, v in snapshot.items() if k != "evidence_sha256"}) != evidence_hash:
        raise ValueError("Immutable evidence checksum mismatch")
    if snapshot["engine_version"] != ENGINE_VERSION or snapshot["source_commit"] != SOURCE_COMMIT:
        raise ValueError("Evidence belongs to a different engine")
    expected_input = digest(
        {key: snapshot[key] for key in ("operation", "parameters", "decision_date")}
    )
    if snapshot["input_sha256"] != expected_input:
        raise ValueError("Input identity differs from frozen parameters")
    acquired = datetime.fromisoformat(snapshot["acquired_at"])
    if acquired.tzinfo is None or acquired > datetime.now(UTC):
        raise ValueError("Invalid evidence availability timestamp")
    decision = date.fromisoformat(snapshot["decision_date"])
    if decision != acquired.astimezone(UTC).date():
        raise ValueError("Captured sources cannot be backdated to an earlier decision")
    for record in snapshot["records"].values():
        if datetime.fromisoformat(record["available_at"]) > acquired:
            raise ValueError("Source available after evidence capture")
    with evidence_session(records=snapshot["records"]):
        with collect_forecast_diagnostics() as diagnostics:
            payload = calculate(snapshot["operation"], snapshot["parameters"], decision)
    if snapshot["operation"] in {"climate_step", "climate_compare", "sutherland_2027"}:
        diagnostics.extend(climate_diagnostics(payload))
    interpreted = interpretation(snapshot["land_status"], diagnostics)
    if interpreted != snapshot["interpretation"]:
        raise ValueError("Interpretation differs from the frozen calculation evidence")
    if digest(payload) != snapshot["calculation_sha256"]:
        raise ValueError("Offline calculation differs from captured acquisition result")
    receipts = [
        {k: v for k, v in record.items() if k != "body_base64"}
        for record in snapshot["records"].values()
    ]
    receipt = files("field_shift_rotation_model").joinpath("upstream-receipt.json").read_bytes()
    receipts.append(
        {
            "source_url": "https://github.com/matveyola/nasa_field_shift/tree/"
            + SOURCE_COMMIT
            + "/research/rotation-handoff",
            "sha256": hashlib.sha256(receipt).hexdigest(),
            "bytes": len(receipt),
            "available_at": snapshot["acquired_at"],
            "kind": "packaged_reference",
        }
    )
    return {
        "operation": snapshot["operation"],
        "status": "available",
        "data_kind": KINDS[snapshot["operation"]],
        "empirical_status": "not_validated",
        "engine_version": ENGINE_VERSION,
        "source_commit": SOURCE_COMMIT,
        "input_sha256": snapshot["input_sha256"],
        "evidence_sha256": evidence_hash,
        "acquired_at": snapshot["acquired_at"],
        "payload": payload,
        **interpreted,
        "source_receipts": receipts,
        "limitations": LIMITATIONS,
        "reasons": [],
    }


def dispatch(operation, payload):
    if operation == "catalog":
        return catalog()
    if operation == "acquire":
        return acquire(payload)
    if operation == "evaluate":
        return evaluate(payload)
    raise ValueError("Unsupported runtime phase")
