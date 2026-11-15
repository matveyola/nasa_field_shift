from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.modules.farm.models import FARM_ID
from field_shift.modules.onboarding.models import FieldSetup
from field_shift.modules.onboarding.schemas import SetupInputs


def completed_setup_inputs(session: Session, field_ids: Iterable[str]) -> dict[str, SetupInputs]:
    ids = tuple(field_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(FieldSetup.field_id, FieldSetup.inputs).where(
            FieldSetup.farm_id == FARM_ID,
            FieldSetup.field_id.in_(ids),
            FieldSetup.completed_at.is_not(None),
        )
    )
    return {field_id: SetupInputs.model_validate(inputs) for field_id, inputs in rows}
