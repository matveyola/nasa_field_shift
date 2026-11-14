from datetime import date

from sqlalchemy.orm import Session

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.demo_fields_naip import DEMO_FIELDS_NAIP_2026
from field_shift.modules.farm.models import FARM_ID, Farm, Observation
from field_shift.modules.fields.models import Field


def seed_demo(session: Session) -> None:
    if session.get(Farm, FARM_ID) is not None:
        return
    session.add(
        Farm(
            id=FARM_ID,
            name="Oakridge Farm",
            season=2026,
            next_field_number=4,
            irrigation_available=False,
            priority="Reduce water stress",
            equipment=None,
            budget=None,
        )
    )
    session.flush()
    for field_id, name, crop, area, ring in DEMO_FIELDS_NAIP_2026:
        session.add(
            Field(
                id=field_id,
                farm_id=FARM_ID,
                name=name,
                crop=crop,
                area_ha=area,
                season=2026,
                geometry={"type": "Polygon", "coordinates": [ring]},
            )
        )
    session.flush()
    session.add_all(
        [
            Observation(
                field_id="F-03",
                kind="soil_photo",
                text="Example observation at the southern edge of the field.",
                date=date(2026, 8, 1),
            ),
            Observation(
                field_id="F-01",
                kind="field_note",
                text="Example note recorded after a field walk.",
                date=date(2026, 7, 28),
            ),
        ]
    )
    session.commit()


if __name__ == "__main__":
    with build_session_factory(get_settings().database_url)() as database:
        seed_demo(database)
