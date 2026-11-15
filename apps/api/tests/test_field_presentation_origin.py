"""Keep seed provenance separate from real weather and user registrations."""

from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.seed import seed_demo


def test_exact_seed_boundaries_are_labeled_but_user_registrations_are_not():
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
    try:
        with TestClient(create_app(session_factory=factory)) as client:
            fields = client.get("/api/fields").json()
            assert len(fields) == 3
            assert all(row["boundary_origin"] == "example" for row in fields)
            # Same name and geometry do not imply that a new user record was seeded.
            source = fields[0]
            payload = {key: source[key] for key in ("name", "crop", "area_ha", "geometry")}
            response = client.post(
                "/api/fields", json=payload, headers={"X-Field-Shift-Request": "1"}
            )
            assert response.status_code == 201
            assert response.json()["boundary_origin"] == "registered"
            assert response.json()["id"] != source["id"]
    finally:
        factory.kw["bind"].dispose()
