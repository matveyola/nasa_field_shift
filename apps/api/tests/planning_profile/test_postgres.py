"""Run explicitly with ISSUE3_DATABASE_URL pointing to a disposable owned database."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_persistence import verify_migration
from test_persistence import workspace as workspace

from field_shift.db import build_session_factory
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository

pytestmark = pytest.mark.integration


def test_postgres_migration_and_concurrent_cas(workspace):
    url = os.environ["ISSUE3_DATABASE_URL"]
    verify_migration(url)
    factory = build_session_factory(url)
    _, payload = workspace

    def write(request_id):
        with factory() as session:
            try:
                return (
                    ProfileRepository(session)
                    .write(
                        "1",
                        "F-01",
                        payload.model_copy(
                            update={
                                "request_id": request_id,
                            }
                        ),
                    )
                    .profile_revision
                )
            except ProfileError as exc:
                return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(write, ["race-a", "race-b"]))
        assert sorted(map(str, outcomes)) == ["1", "stale"]
        with factory() as session:
            assert ProfileRepository(session).latest("1", "F-01").profile_revision == 1
    finally:
        factory.kw["bind"].dispose()
