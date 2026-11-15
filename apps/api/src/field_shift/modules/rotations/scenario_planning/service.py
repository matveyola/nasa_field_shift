"""Durable runs and human application. Caller owns commit/rollback.

A mandatory SnapshotResolver authenticates profiles/weather and locks their current
versions (including rulebook, cultivar, model artifact and parameter hashes).
No client model card, profile or hash is a source of authority.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from field_shift_contracts.planning_v1.field import CropAssignment
from field_shift_contracts.planning_v1.planning import ApplyPlanResult, PlanningRun
from sqlalchemy import select

from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field

from .common import canonical, digest, reason
from .models import PlanningApplyRow, PlanningCommitment, PlanningRunRow


class PlanningFailure(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.reason = reason(code, message, "planning-service", "result")


class SnapshotResolver(Protocol):
    def resolve(self, session, request, *, lock: bool) -> tuple[object, dict]:
        """Return trusted complete FarmPlanningRequest and current version vector.

        lock=True holds owning rows/version locks until transaction completion.
        Vector must include profiles, geometry, weather, rulebook, cultivars and
        model artifact/parameter checksums. Compare does not trust client copies.
        """
        ...


def now():
    return datetime.now(UTC)


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RunService:
    def __init__(self, resolver: SnapshotResolver):
        self.resolver = resolver

    def scope(self, session, principal, farm_id, *, write=False):
        if str(principal.farm_id) != farm_id:
            raise PlanningFailure("not_found", "Farm is inaccessible.")
        if write and principal.role not in ("admin", "editor"):
            raise PlanningFailure("forbidden", "Editor access is required.")
        farm = session.scalar(
            select(Farm)
            .where(Farm.id == principal.farm_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if farm is None:
            raise PlanningFailure("not_found", "Farm does not exist.")
        return farm

    def _row(self, session, farm_id, run_id):
        row = session.scalar(
            select(PlanningRunRow)
            .where(PlanningRunRow.id == run_id, PlanningRunRow.farm_id == int(farm_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise PlanningFailure("not_found", "Run is inaccessible.")
        return row

    def submit(self, session, principal, request):
        self.scope(session, principal, request.farm_id, write=True)
        old = session.scalar(
            select(PlanningRunRow).where(
                PlanningRunRow.farm_id == principal.farm_id,
                PlanningRunRow.request_id == request.request_id,
            )
        )
        if old:
            if old.input_hash != digest(request):
                raise PlanningFailure("conflict", "Request ID already has a different payload.")
            return self.read(old)
        trusted, versions = self.resolver.resolve(session, request, lock=True)
        if canonical(trusted) != canonical(request):
            raise PlanningFailure(
                "stale", "Submitted inputs differ from trusted current snapshots."
            )
        if len(canonical(request).encode()) > 8_000_000:
            raise PlanningFailure("invalid_input", "Planning input exceeds 8 MB admission limit.")
        required = {"profiles", "geometry", "weather", "rulebook", "cultivars", "models"}
        if not required <= versions.keys():
            raise PlanningFailure("unavailable", "Incomplete authoritative version vector.")
        # Scope all fields independently of the submitted farm ID.
        for profile in request.fields:
            field = session.get(Field, profile.field_id)
            if field is None or field.farm_id != principal.farm_id:
                raise PlanningFailure("not_found", "Field is inaccessible.")
        self._preserve(session, request)
        timestamp = now()
        run = PlanningRun(
            run_id=str(uuid4()),
            request=request,
            input_sha256=digest(request),
            status="queued",
            attempt=0,
            created_at=timestamp,
            updated_at=timestamp,
            comparison=None,
            error=None,
        )
        row = PlanningRunRow(
            id=run.run_id,
            farm_id=principal.farm_id,
            request_id=request.request_id,
            input_hash=run.input_sha256,
            payload=run.model_dump(mode="json"),
            artifacts=None,
            versions=versions,
            status="queued",
            attempt=0,
            lease_until=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(row)
        session.flush()
        # #7 publishes only this committed run ID, never the input payload.
        return run

    def _preserve(self, session, request):
        commitments = session.scalars(
            select(PlanningCommitment).where(PlanningCommitment.farm_id == int(request.farm_id))
        )
        fields = {f.field_id for f in request.fields}
        seasons = range(request.start_season, request.start_season + request.season_count)
        for c in commitments:
            if c.field_id in fields and c.season in seasons:
                a = CropAssignment.model_validate_json(canonical(c.assignment))
                if a not in request.locks:
                    raise PlanningFailure(
                        "conflict", "Request must preserve confirmed commitments."
                    )

    def read(self, row):
        return PlanningRun.model_validate_json(canonical(row.payload))

    def detail(self, session, principal, farm_id, run_id):
        self.scope(session, principal, farm_id)
        return self.read(self._row(session, farm_id, run_id))

    def list(self, session, principal, farm_id, *, offset=0, limit=20):
        self.scope(session, principal, farm_id)
        if not 0 <= offset <= 10000 or not 1 <= limit <= 50:
            raise PlanningFailure("invalid_input", "Pagination outside supported bounds.")
        return [
            self.read(r)
            for r in session.scalars(
                select(PlanningRunRow)
                .where(PlanningRunRow.farm_id == principal.farm_id)
                .order_by(PlanningRunRow.created_at.desc(), PlanningRunRow.id)
                .offset(offset)
                .limit(limit)
            )
        ]

    def apply(self, session, principal, payload):
        self.scope(session, principal, payload.farm_id, write=True)
        existing = session.scalar(
            select(PlanningApplyRow).where(
                PlanningApplyRow.farm_id == principal.farm_id,
                PlanningApplyRow.request_id == payload.request_id,
            )
        )
        if existing:
            if existing.input_hash != digest(payload):
                raise PlanningFailure("conflict", "Apply ID already has a different payload.")
            result = ApplyPlanResult.model_validate_json(canonical(existing.result))
            return result.model_copy(update={"status": "already_applied"})
        row = self._row(session, payload.farm_id, payload.run_id)
        run = self.read(row)
        if session.scalar(select(PlanningApplyRow).where(PlanningApplyRow.run_id == row.id)):
            raise PlanningFailure("conflict", "Run already applied with another request.")
        if run.status != "succeeded" or not run.comparison:
            raise PlanningFailure("conflict", "Run has no completed comparison.")
        if payload.expected_input_sha256 != run.input_sha256 or sorted(
            payload.expected_snapshots, key=lambda s: s.field_id
        ) != sorted(run.comparison.snapshots, key=lambda s: s.field_id):
            raise PlanningFailure("stale", "Expected immutable input versions do not match.")
        current, versions = self.resolver.resolve(session, run.request, lock=True)
        if (
            digest(current) != run.input_sha256
            or versions != row.versions
            or run.request.weather.stale_after <= now()
        ):
            raise PlanningFailure("stale", "Inputs, model or forecast changed; replan is required.")
        self._preserve(session, run.request)
        alternative = next(
            (p for p in run.comparison.alternatives if p.alternative_id == payload.alternative_id),
            None,
        )
        if alternative is None:
            raise PlanningFailure("invalid_input", "Alternative does not belong to this run.")
        for a in alternative.assignments:
            existing_c = session.get(PlanningCommitment, (principal.farm_id, a.field_id, a.season))
            if existing_c:
                if CropAssignment.model_validate_json(canonical(existing_c.assignment)) != a:
                    raise PlanningFailure(
                        "conflict", "Confirmed crop cannot be changed automatically."
                    )
            else:
                committed = a.model_copy(
                    update={"locked": True, "commitment_id": a.commitment_id or str(uuid4())}
                )
                session.add(
                    PlanningCommitment(
                        farm_id=principal.farm_id,
                        field_id=a.field_id,
                        season=a.season,
                        commitment_id=committed.commitment_id,
                        assignment=committed.model_dump(mode="json"),
                    )
                )
        result = ApplyPlanResult(
            request_id=payload.request_id, status="applied", applied_at=now(), reasons=[]
        )
        session.add(
            PlanningApplyRow(
                id=str(uuid4()),
                farm_id=principal.farm_id,
                run_id=row.id,
                request_id=payload.request_id,
                input_hash=digest(payload),
                alternative_id=payload.alternative_id,
                result=result.model_dump(mode="json"),
            )
        )
        session.flush()
        return result

    def replan(self, session, principal, payload):
        old = self.detail(session, principal, payload.request.farm_id, payload.previous_run_id)
        if old.request.request_id == payload.request.request_id:
            raise PlanningFailure("conflict", "Replan requires a new request ID.")
        previous = {a.commitment_id: a for a in old.request.locks}
        for p in old.comparison.alternatives if old.comparison else []:
            for a in p.assignments:
                if a.locked:
                    previous[a.commitment_id] = a
        for c in session.scalars(
            select(PlanningCommitment).where(PlanningCommitment.farm_id == principal.farm_id)
        ):
            if c.field_id in {f.field_id for f in payload.request.fields} and c.season in range(
                payload.request.start_season,
                payload.request.start_season + payload.request.season_count,
            ):
                previous[c.commitment_id] = CropAssignment.model_validate_json(
                    canonical(c.assignment)
                )
        if set(previous) - set(payload.preserve_commitment_ids):
            raise PlanningFailure("conflict", "Replan must name every existing commitment.")
        for cid in payload.preserve_commitment_ids:
            if cid not in previous or previous[cid] not in payload.request.locks:
                raise PlanningFailure(
                    "conflict", "Replan must retain referenced commitments unchanged."
                )
        run = self.submit(session, principal, payload.request)
        row = self._row(session, payload.request.farm_id, run.run_id)
        lineage = {
            "previous_run_id": old.run_id,
            "preserve_commitment_ids": sorted(payload.preserve_commitment_ids),
            "previous_input_sha256": old.input_sha256,
            "new_input_sha256": run.input_sha256,
            "changed_sections": [
                k
                for k, v in old.request.model_dump(mode="json").items()
                if run.request.model_dump(mode="json").get(k) != v
            ],
        }
        if row.lineage is not None and row.lineage != lineage:
            raise PlanningFailure("conflict", "Replan request ID has another lineage.")
        row.lineage = lineage
        return run

    def claim(self, session, run_id):
        row = session.scalar(
            select(PlanningRunRow)
            .where(PlanningRunRow.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status in ("succeeded", "failed", "cancelled"):
            return None
        if row.status == "running" and aware(row.lease_until) > now():
            return None
        if row.attempt >= 3:
            self.finish(
                session,
                row.id,
                row.attempt,
                error=reason(
                    "attempts_exhausted", "Execution lease retries exhausted.", row.id, "result"
                ),
            )
            return None
        row.attempt += 1
        row.status = "running"
        row.lease_until = now() + timedelta(seconds=360)
        run = self.read(row).model_copy(
            update={"status": "running", "attempt": row.attempt, "updated_at": now()}
        )
        row.payload = run.model_dump(mode="json")
        session.flush()
        return run

    def finish(self, session, run_id, attempt, *, comparison=None, artifacts=None, error=None):
        row = session.scalar(
            select(PlanningRunRow)
            .where(PlanningRunRow.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.status != "running"
            or row.attempt != attempt
            or (row.lease_until and aware(row.lease_until) <= now() and error is None)
        ):
            return False
        run = self.read(row)
        data = run.model_dump(mode="json")
        data.update(
            status="failed" if error else "succeeded",
            updated_at=now().isoformat(),
            comparison=comparison.model_dump(mode="json") if comparison else None,
            error=error.model_dump(mode="json") if error else None,
        )
        validated = PlanningRun.model_validate_json(canonical(data))
        row.payload = validated.model_dump(mode="json")
        row.status = validated.status
        row.artifacts = artifacts
        row.updated_at = now()
        row.lease_until = None
        session.flush()
        return True

    def cancel(self, session, principal, farm_id, run_id):
        self.scope(session, principal, farm_id, write=True)
        row = self._row(session, farm_id, run_id)
        if row.status in ("queued", "running"):
            row.status = "cancelled"
            row.payload = (
                self.read(row)
                .model_copy(update={"status": "cancelled", "updated_at": now()})
                .model_dump(mode="json")
            )
            row.lease_until = None
        return self.read(row)
