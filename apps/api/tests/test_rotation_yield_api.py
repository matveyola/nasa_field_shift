from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.fields.models import Field, FieldBoundaryVersion
from field_shift.modules.rotations import service, yield_service
from field_shift.seed import seed_demo


def test_yield_comparison_is_scoped_to_trial_eligible_plan_years(monkeypatch):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    calls = []

    def estimate(**inputs):
        calls.append(inputs)
        if inputs["crop"] == "Maize" and inputs["preceding_crop"] == "Wheat":
            return SimpleNamespace(delta_t_ha=1.2, lower_t_ha=0.1, upper_t_ha=2.4)
        return None

    model = SimpleNamespace(
        version="test-yield",
        promoted=False,
        validation={"training_sites": 7},
        estimate=estimate,
    )
    monkeypatch.setattr(yield_service, "load_rotation_yield_model", lambda: model)
    endpoint = "/api/rotations/yield?field_id=F-03&season=2026"
    try:
        with TestClient(create_app(session_factory=factory)) as client:
            monkeypatch.setenv("ROTATION_YIELD_ENABLE_EXPERIMENTAL", "0")
            unavailable = client.get(endpoint).json()
            assert unavailable["status"] == "unavailable"
            assert not calls
            assert all(
                row["change_t_ha"] is None
                for plan in unavailable["plans"]
                for row in plan["estimates"]
            )

            monkeypatch.setenv("ROTATION_YIELD_ENABLE_EXPERIMENTAL", "1")
            comparison = client.get(endpoint).json()
            assert comparison["status"] == "experimental"
            assert comparison["training_sites"] == 7
            assert comparison["model_version"] == "test-yield"
            assert len(calls) == 6
            assert calls[0]["preceding_crop"] == "Maize"
            assert calls[-1]["crop_sequence"] == ("Soy", "Wheat", "Maize")
            assert -94 < calls[-1]["longitude"] < -93
            assert 41 < calls[-1]["latitude"] < 42
            row = comparison["plans"][1]["estimates"][-1]
            assert row == {
                "year": 2029,
                "crop": "Maize",
                "previous_crop": "Wheat",
                "change_t_ha": 1.2,
                "lower_t_ha": 0.1,
                "upper_t_ha": 2.4,
                "reason": None,
            }
            assert comparison["plans"][1]["estimates"][0]["change_t_ha"] is None

            different_season = client.get(endpoint.replace("2026", "2027")).json()
            assert different_season["plans"][0]["estimates"][0]["previous_crop"] is None
            assert different_season["plans"][0]["estimates"][0]["change_t_ha"] is None

            model.promoted = True
            monkeypatch.setenv("ROTATION_YIELD_ENABLE_EXPERIMENTAL", "0")
            assert client.get(endpoint).json()["status"] == "validated"
    finally:
        engine.dispose()


def test_yield_reads_saved_plan_without_running_water_simulations(monkeypatch):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)

    def unexpected_simulation(*args, **kwargs):
        raise AssertionError("The yield endpoint must not calculate a water balance.")

    scope = {"field_id": "F-03", "season": 2026}
    crops = ["Wheat", "Maize", "Soy"]
    try:
        with TestClient(create_app(session_factory=factory)) as client:
            saved = client.put("/api/rotations/plans/plan-b", json={**scope, "crops": crops})
            assert saved.status_code == 200
            baseline = client.get("/api/rotations", params=scope)
            assert baseline.status_code == 200

            monkeypatch.setattr(service, "calculate_water_balance", unexpected_simulation)
            monkeypatch.setattr(yield_service, "load_rotation_yield_model", lambda: None)
            comparison = client.get("/api/rotations/yield", params=scope)
            assert comparison.status_code == 200
            estimates = comparison.json()["plans"][1]["estimates"]
            assert [{"crop": row["crop"], "year": row["year"]} for row in estimates] == (
                baseline.json()["plans"][1]["crops"]
            )
            assert [row["crop"] for row in estimates] == crops
    finally:
        engine.dispose()


def test_yield_uses_boundary_effective_for_selected_season(monkeypatch):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    calls = []
    with factory() as session:
        seed_demo(session)
        field = session.get(Field, "F-03")
        session.add(
            FieldBoundaryVersion(
                field_id=field.id,
                revision=0,
                effective_season=2026,
                geometry=field.geometry,
                area_ha=field.area_ha,
                source="created",
                created_at=datetime.now(UTC),
            )
        )
        field.geometry = {
            "type": "Polygon",
            "coordinates": [[[31, 48], [31.01, 48], [31, 48.01], [31, 48]]],
        }
        field.boundary_revision = 1
        session.commit()

    def estimate(**inputs):
        calls.append(inputs)
        return None

    monkeypatch.setattr(
        yield_service,
        "load_rotation_yield_model",
        lambda: SimpleNamespace(
            promoted=True,
            version="test",
            validation={},
            estimate=estimate,
        ),
    )
    try:
        with factory() as session:
            yield_service.get_yield_comparison(session, "F-03", 2026)
        assert calls
        assert all(-94 < call["longitude"] < -93 and 41 < call["latitude"] < 42 for call in calls)
    finally:
        engine.dispose()
