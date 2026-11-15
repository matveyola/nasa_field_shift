"""Calendar transport is intentionally incapable of supplying an invented profit."""

import pytest
from pydantic import ValidationError

from field_shift.modules.fields.planting_opportunities.schemas import Opportunity


def row(**changes):
    return {
        "id": "reference-Wheat1-2027",
        "crop_id": "Wheat",
        "cycle_id": "Wheat1",
        "system": "rainfed",
        "calendar_kind": "regional_typical_month",
        "date_precision": "month",
        "calendar_status": "upcoming",
        "planting_period": {"start": "2027-10-01", "end": "2027-10-31"},
        "maturity_period": {"start": "2028-07-01", "end": "2028-07-31"},
        "maturity_basis": "regional_maturity_month",
        "harvest_year": 2028,
        "next_reference_date": "2027-10-01",
        "eligibility": "review_required",
        "missing_data": ["field_weather", "economics"],
        "reasons": ["Monthly reference only"],
        "source": None,
        "native_crop_profile_available": True,
        **changes,
    }


def test_monthly_reference_retains_sowing_and_harvest_year_distinction():
    parsed = Opportunity.model_validate(row())
    assert parsed.harvest_year == 2028
    assert parsed.planting_period.start.year == 2027
    assert parsed.profit is None and parsed.profit_status == "unassessed"
    assert parsed.model_evaluation_status == "not_run"


@pytest.mark.parametrize(
    "changes", [{"profit": 100}, {"profit_status": "profitable"}, {"calendar_status": "optimal"}]
)
def test_transport_rejects_unsupported_claims(changes):
    with pytest.raises(ValidationError):
        Opportunity.model_validate(row(**changes))
