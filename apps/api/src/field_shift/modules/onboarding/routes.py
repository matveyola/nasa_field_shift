from uuid import UUID

from fastapi import APIRouter

from field_shift.db import SessionDep
from field_shift.modules.onboarding import service
from field_shift.modules.onboarding.schemas import SetupInputs, SetupRead

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/fields/{field_id}", response_model=SetupRead)
def field_setup(field_id: str, session: SessionDep):
    return service.get_field_setup(session, field_id)


@router.get("/{setup_id}", response_model=SetupRead)
def setup(setup_id: UUID, session: SessionDep):
    return service.get_setup(session, setup_id)


@router.put("/{setup_id}", response_model=SetupRead)
def save(setup_id: UUID, payload: SetupInputs, session: SessionDep):
    return service.save_setup(session, setup_id, payload)


@router.post("/{setup_id}/complete", response_model=SetupRead)
def complete(setup_id: UUID, session: SessionDep):
    return service.complete_setup(session, setup_id)
