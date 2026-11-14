"""Farm-scoped, serialized writes with compare-and-swap and immutable replay."""

import hashlib
import json
import math
from uuid import uuid4

from field_shift_contracts.planning_v1 import FieldProfileSnapshot
from field_shift_science.fields.geometry import area_hectares
from shapely.geometry import shape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from field_shift.modules.fields.models import Field

from .models import ProfileSnapshot


class ProfileError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def canonical_hash(model):
    return hashlib.sha256(
        json.dumps(
            model.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def validate_geometry(profile):
    polygon = shape(profile.geometry.model_dump(mode="json"))
    if not polygon.is_valid or polygon.is_empty:
        raise ProfileError("invalid_input", "Invalid polygon topology or holes")
    rings = profile.geometry.coordinates
    area = area_hectares(rings[0]) - sum(area_hectares(ring) for ring in rings[1:])
    if area <= 0 or not math.isclose(area, profile.area_ha, rel_tol=0.02, abs_tol=0.01):
        raise ProfileError("invalid_input", "Area differs from WGS84 polygon area")
    return polygon


def require_field(session, farm_id, field_id, lock=False):
    statement = select(Field).where(Field.id == field_id, Field.farm_id == farm_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    field = session.scalar(statement)
    if field is None:
        raise ProfileError("not_found", "Field not found")
    return field


def decode(row):
    return FieldProfileSnapshot.model_validate_json(json.dumps(row.document))


class ProfileRepository:
    def __init__(self, session):
        self.session = session

    def get_snapshot(self, farm_id, field_id, snapshot_id):
        require_field(self.session, int(farm_id), field_id)
        row = self.session.scalar(
            select(ProfileSnapshot).where(
                ProfileSnapshot.snapshot_id == snapshot_id,
                ProfileSnapshot.farm_id == int(farm_id),
                ProfileSnapshot.field_id == field_id,
            )
        )
        if row is None:
            raise ProfileError("not_found", "Snapshot not found")
        return decode(row)

    def latest(self, farm_id, field_id):
        field = require_field(self.session, int(farm_id), field_id)
        row = self.session.scalar(
            select(ProfileSnapshot)
            .where(
                ProfileSnapshot.field_id == field_id,
            )
            .order_by(ProfileSnapshot.revision.desc())
            .limit(1)
        )
        if row is None:
            raise ProfileError("not_found", "Profile not found")
        if row.geometry_revision != field.boundary_revision:
            raise ProfileError("stale", "Boundary changed; write a new profile")
        return decode(row)

    def write(self, farm_id, field_id, request):
        profile = request.profile
        if profile.farm_id != str(farm_id) or profile.field_id != field_id:
            raise ProfileError("invalid_input", "Path and profile scope differ")
        field = require_field(self.session, int(farm_id), field_id, lock=True)
        fingerprint = canonical_hash(request)
        replay = self.session.scalar(
            select(ProfileSnapshot).where(
                ProfileSnapshot.farm_id == int(farm_id),
                ProfileSnapshot.request_id == request.request_id,
            )
        )
        if replay:
            if replay.request_hash != fingerprint:
                raise ProfileError("conflict", "Request ID reused with different content")
            return decode(replay)
        current = self.session.scalar(
            select(ProfileSnapshot)
            .where(
                ProfileSnapshot.field_id == field_id,
            )
            .order_by(ProfileSnapshot.revision.desc())
            .limit(1)
        )
        if (current.revision if current else None) != request.expected_revision:
            raise ProfileError("stale", "Profile revision changed")
        polygon = validate_geometry(profile)
        if profile.geometry_revision != field.boundary_revision or not polygon.equals(
            shape(field.geometry)
        ):
            raise ProfileError("stale", "Profile boundary differs from current field")
        revision = current.revision + 1 if current else 1
        # Snapshot identifiers/revisions are service-owned; the client cannot overwrite history.
        saved = FieldProfileSnapshot.model_validate_json(
            json.dumps(
                {
                    **profile.model_dump(mode="json"),
                    "snapshot_id": str(uuid4()),
                    "profile_revision": revision,
                }
            )
        )
        row = ProfileSnapshot(
            snapshot_id=saved.snapshot_id,
            farm_id=int(farm_id),
            field_id=field_id,
            revision=revision,
            geometry_revision=saved.geometry_revision,
            request_id=request.request_id,
            request_hash=fingerprint,
            document=saved.model_dump(mode="json"),
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ProfileError("conflict", "Concurrent profile write; reload and retry") from exc
        return saved
