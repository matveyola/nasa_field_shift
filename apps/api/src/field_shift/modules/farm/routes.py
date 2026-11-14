from fastapi import APIRouter, Response, status

from field_shift.db import SessionDep
from field_shift.modules.farm import service
from field_shift.modules.farm.schemas import (
    DefaultsPatch,
    FarmDefaults,
    FarmRead,
    ObservationCreate,
    ObservationRead,
)

router = APIRouter(prefix="/farm", tags=["farm"])


@router.get("", response_model=FarmRead)
def get_farm(session: SessionDep):
    return service.get_farm(session)


@router.patch("/defaults", response_model=FarmDefaults)
def patch_defaults(payload: DefaultsPatch, session: SessionDep):
    return service.update_defaults(session, payload)


@router.post("/observations", status_code=status.HTTP_201_CREATED, response_model=ObservationRead)
def create_observation(payload: ObservationCreate, session: SessionDep):
    return service.create_observation(session, payload)


class ObservationPhotoResponse(Response):
    media_type = "image/jpeg"


@router.get("/observations/{observation_id}/photo", response_class=ObservationPhotoResponse)
def get_observation_photo(observation_id: int, session: SessionDep):
    return ObservationPhotoResponse(
        content=service.observation_photo(session, observation_id),
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-site",
        },
    )
