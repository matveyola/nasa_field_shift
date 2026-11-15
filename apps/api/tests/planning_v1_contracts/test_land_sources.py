"""Additive land-source binding with preserved pre-extension canonical identities."""

import hashlib
import json
from importlib.resources import files

import pytest
from field_shift_contracts.planning_v1 import FieldProfileSnapshot, ProfileWriteRequest
from pydantic import ValidationError


def legacy_document():
    return json.loads(
        files("field_shift_contracts.planning_v1")
        .joinpath("data/planning-contracts.json")
        .read_text()
    )["field_profile"]


def digest(model):
    return hashlib.sha256(
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def test_legacy_profile_and_request_keep_exact_before_extension_hashes():
    # These hashes were captured using frozen55ee251's installed contracts before
    # adding land_source_ids. Empty bindings must not rewrite durable identities.
    document = legacy_document()
    parsed = FieldProfileSnapshot.model_validate_json(json.dumps(document))
    assert parsed.land_source_ids == []
    assert "land_source_ids" not in parsed.model_dump(mode="json")
    assert digest(parsed) == "4fd5820c3e74ce275e5e9267b816a041c950639aee6894284d392403baf44918"
    request = ProfileWriteRequest(
        request_id="legacy-land-proof", expected_revision=None, profile=parsed
    )
    assert digest(request) == "63fdeb355ce688eebdf1c31510941f9544395d04810f73a34cc48dca4951f4c6"
    explicit_empty = FieldProfileSnapshot.model_validate_json(
        json.dumps(document | {"land_source_ids": []})
    )
    assert digest(explicit_empty) == digest(parsed)


def test_explicit_source_binding_roundtrips_and_changes_identity():
    document = legacy_document()
    old = FieldProfileSnapshot.model_validate_json(json.dumps(document))
    document["land_source_ids"] = [document["sources"][0]["id"]]
    linked = FieldProfileSnapshot.model_validate_json(json.dumps(document))
    assert linked.model_dump(mode="json")["land_source_ids"] == document["land_source_ids"]
    assert FieldProfileSnapshot.model_validate_json(linked.model_dump_json()) == linked
    assert digest(linked) != digest(old)


@pytest.mark.parametrize(
    "links",
    [
        ["missing-source"],
        ["synthetic-input", "synthetic-input"],
        [42],
        None,
        ["synthetic-input"] * 201,
    ],
)
def test_land_sources_reject_unresolved_duplicate_nonidentifiers_and_overflow(links):
    with pytest.raises(ValidationError):
        FieldProfileSnapshot.model_validate_json(
            json.dumps(legacy_document() | {"land_source_ids": links})
        )
