"""Farmer-confirmed planning constraints: the host records only the farmer's decisions, for one
field, planning season and boundary, and the agent sees them only in the context the API signs."""

# ruff: noqa: F811  (the `app` fixture comes from test_assistant_selected_run)

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from agent_contract_schema import validate_agent_wire
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from test_assistant_selected_run import (  # noqa: F401
    _open_stream,
    _widget_turn,
    app,
    charts,
    save_run,
)

from field_shift.db import build_engine
from field_shift.modules.agent.models import PlanningConstraint
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field

URL = "/api/agent/constraints/F-03/2026"
CONTEXT = "/api/agent/assistant/F-03/2026/{}/field-context?locale=en"
NO_IRRIGATION = {
    "id": "c_irrigation",
    "type": "irrigation_available",
    "value": False,
    "status": "confirmed",
    "source": "farmer_text",
    "evidence": "I don't have irrigation",
}
NO_SUNFLOWER = {
    "id": "c_excluded",
    "type": "excluded_crops",
    "value": ["sunflower"],
    "status": "confirmed",
    "source": "farmer_text",
    "evidence": "exclude sunflower",
}


def decision(*constraints, field_id="F-03", revision=0, replaces=None, **envelope) -> dict:
    body = {
        "field_id": field_id,
        "envelope": {
            "id": "confirm.turn_1",
            "field_context_revision": revision,
            "trigger": "chip",
            "label": "Confirm",
            "action": {"type": "compare.set_constraints", "constraints": list(constraints)},
            **envelope,
        },
    }
    if replaces is not None:
        body["replaces"] = replaces  # the farmer saw these earlier decisions and replaces them
    return body


UUID_TOKEN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def record(constraint: dict) -> dict:
    """How the farmer's record lists a stored decision (its token aside: see bare)."""
    return {**constraint, "id": f"pc_{constraint['type']}"}


def bare(items: list[dict]) -> list[dict]:
    """Listed decisions without their opaque tokens, after checking each has one."""
    assert all(UUID_TOKEN.match(item["token"]) for item in items)
    return [{k: v for k, v in item.items() if k != "token"} for item in items]


def token_of(client, kind: str) -> str:
    return next(c["token"] for c in client.get(URL).json()["constraints"] if c["type"] == kind)


def withdraw(client, kind: str, token: str, revision: int = 0, url: str = URL):
    return client.delete(f"{url}/{kind}?boundary_revision={revision}&token={token}")


def stored(app) -> list[PlanningConstraint]:
    with app.state.session_factory() as session:
        return list(session.query(PlanningConstraint).order_by(PlanningConstraint.type))


def context_constraints(client, scenario="baseline") -> list[dict]:
    return client.get(CONTEXT.format(scenario)).json().get("constraints", [])


# ---------------------------------------------------------------- the farmer decides


def test_confirmed_constraints_become_host_state_for_this_field_and_season(app):
    with TestClient(app) as client:
        assert context_constraints(client) == []  # nothing until the farmer decides
        response = client.post(URL, json=decision(NO_IRRIGATION, NO_SUNFLOWER))
        assert response.status_code == 200
        listed = client.get(URL).json()["constraints"]
        in_context = context_constraints(client)
    expected = [record(NO_SUNFLOWER), record(NO_IRRIGATION)]
    assert response.json()["constraints"] == listed  # the farmer's own record
    assert bare(listed) == expected
    assert response.json()["replaced"] == []
    # The agent gets structured decisions only: the farmer decided in the app, and the quoted
    # words stay in the farm record (like scouting notes), never in the signed context.
    assert in_context == [
        {
            "id": c["id"],
            "type": c["type"],
            "value": c["value"],
            "status": "confirmed",
            "source": "farmer_ui",
        }
        for c in expected
    ]
    rows = stored(app)
    assert [(r.field_id, r.season, r.boundary_revision, r.decided_by) for r in rows] == [
        ("F-03", 2026, 0, None),  # local mode: the shared workspace, no member id
    ] * 2


def test_a_planning_preference_never_changes_the_farm_defaults(app):
    with app.state.session_factory() as session:
        before = session.get(Farm, 1).irrigation_available
    with TestClient(app) as client:
        farm_before = client.get("/api/farm").json()
        yes = {**NO_IRRIGATION, "value": True, "evidence": "we irrigate"}
        assert client.post(URL, json=decision(yes)).status_code == 200
        assert client.get("/api/farm").json() == farm_before
    with app.state.session_factory() as session:
        assert session.get(Farm, 1).irrigation_available == before


def test_an_inferred_constraint_needs_the_farmers_approval(app):
    inferred = {
        "id": "c_len",
        "type": "rotation_length_years",
        "value": 4,
        "source": "agent_inferred",
        "confidence": 0.6,
    }
    with TestClient(app) as client:
        refused = client.post(URL, json=decision({**inferred, "status": "proposed"}))
        assert refused.status_code == 409
        assert refused.json() == {
            "detail": "Only constraints the farmer confirmed or rejected can be saved."
        }
        assert stored(app) == []
        approved = client.post(URL, json=decision({**inferred, "status": "confirmed"}))
    assert approved.status_code == 200
    [saved] = approved.json()["constraints"]
    # Still marked as the agent's inference, now confirmed by the farmer.
    assert saved["source"] == "agent_inferred" and saved["status"] == "confirmed"
    assert saved["confidence"] == 0.6


def test_a_rejection_is_recorded_as_rejected(app):
    rejected = {**NO_IRRIGATION, "status": "rejected"}
    with TestClient(app) as client:
        response = client.post(URL, json=decision(rejected))
        assert response.status_code == 200
        [constraint] = context_constraints(client)
    assert constraint["status"] == "rejected"  # the agent ignores rejected constraints
    assert [r.status for r in stored(app)] == ["rejected"]


def test_the_same_decision_again_needs_no_acknowledgement(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_SUNFLOWER))
        before = token_of(client, "excluded_crops")
        again = client.post(URL, json=decision(NO_SUNFLOWER))
    assert again.status_code == 200 and again.json()["replaced"] == []
    # Every write is a new decision record: a view of the earlier one is stale from now on.
    assert [r.token for r in stored(app)] != [before]


def test_a_withdrawn_decision_leaves_no_preference(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION, NO_SUNFLOWER))
        seen = token_of(client, "irrigation_available")
        response = withdraw(client, "irrigation_available", seen)
        assert response.status_code == 200
        assert [c["type"] for c in response.json()["constraints"]] == ["excluded_crops"]
        # Removing it again names a decision that no longer exists: nothing else is touched.
        assert withdraw(client, "irrigation_available", seen).status_code == 409
        assert withdraw(client, "soil_ph", seen).status_code == 422  # not a constraint type
        assert withdraw(client, "excluded_crops", "1").status_code == 422  # not a token
        assert client.delete(URL + "/excluded_crops").status_code == 422  # no precondition
        assert client.delete(URL + "/excluded_crops?boundary_revision=0").status_code == 422
    assert [r.type for r in stored(app)] == ["excluded_crops"]


# ---------------------------------------------------------------- replacing an earlier decision

MAIZE = {**NO_SUNFLOWER, "value": ["maize"], "evidence": "exclude maize"}


def test_changing_an_earlier_decision_is_refused_until_the_farmer_acknowledges_it(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_SUNFLOWER))
        # An ordinary confirmation click on "exclude maize" never discards "exclude sunflower".
        refused = client.post(URL, json=decision(MAIZE))
        assert refused.status_code == 409
        body = refused.json()
        assert body["detail"] == "Confirm that this replaces your earlier decision."
        assert body["code"] == "replacement_needs_confirmation"
        [replacement] = body["replacements"]
        assert replacement["proposed"] == MAIZE
        assert bare([replacement["current"]]) == [record(NO_SUNFLOWER)]
        assert replacement["current"]["token"] == token_of(client, "excluded_crops")
        assert bare(client.get(URL).json()["constraints"]) == [record(NO_SUNFLOWER)]  # unchanged
        # The farmer saw exactly that decision (its token) and chose to replace it.
        seen = refused.json()["replacements"][0]["current"]["token"]
        confirmed = client.post(
            URL, json=decision(MAIZE, replaces=[{"type": "excluded_crops", "token": seen}])
        )
    assert confirmed.status_code == 200
    assert bare(confirmed.json()["replaced"]) == [record(NO_SUNFLOWER)]
    assert bare(confirmed.json()["constraints"]) == [record(MAIZE)]


def test_cancelling_a_replacement_leaves_the_earlier_decision(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        before = client.get(URL).json()
        # The farmer keeps the earlier decision: the app sends nothing more.
        rejected = {**NO_IRRIGATION, "status": "rejected"}
        assert client.post(URL, json=decision(rejected)).status_code == 409
        assert client.get(URL).json() == before
    assert [r.status for r in stored(app)] == ["confirmed"]
    assert before["constraints"][0]["token"] == stored(app)[0].token  # not even rewritten


def test_an_acknowledgement_of_a_decision_that_changed_since_is_stale(app):
    yes = {**NO_IRRIGATION, "value": True, "evidence": "we irrigate"}
    rejected = {**NO_IRRIGATION, "status": "rejected"}
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        ack_v1 = [
            {"type": "irrigation_available", "token": token_of(client, "irrigation_available")}
        ]
        # Two tabs reviewed the same decision. The first replacement wins...
        assert client.post(URL, json=decision(yes, replaces=ack_v1)).status_code == 200
        # ...and the second, approved against the earlier decision, cannot overwrite the new one.
        stale = client.post(URL, json=decision(rejected, replaces=ack_v1))
        assert stale.status_code == 409
        assert stale.json() == {
            "detail": "This decision changed since you reviewed it. Review the current one again."
        }
        # An acknowledgement of a decision that is gone (withdrawn) is stale too.
        newer = token_of(client, "irrigation_available")
        assert withdraw(client, "irrigation_available", newer).status_code == 200
        gone = client.post(URL, json=decision(rejected, replaces=ack_v1))
        assert gone.status_code == 409
    assert stored(app) == []


def test_a_request_with_several_decisions_is_saved_whole_or_not_at_all(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_SUNFLOWER))
        # A new kind (irrigation) next to a change that needs acknowledgement: nothing is saved.
        refused = client.post(URL, json=decision(NO_IRRIGATION, MAIZE))
        assert refused.status_code == 409
        assert [r["current"]["type"] for r in refused.json()["replacements"]] == ["excluded_crops"]
        assert bare(client.get(URL).json()["constraints"]) == [record(NO_SUNFLOWER)]
        # A stale acknowledgement for one kind also saves none of the others.
        unknown = [{"type": "excluded_crops", "token": str(uuid4())}]
        stale = decision(NO_IRRIGATION, MAIZE, replaces=unknown)
        assert client.post(URL, json=stale).status_code == 409
        assert bare(client.get(URL).json()["constraints"]) == [record(NO_SUNFLOWER)]
        seen = [{"type": "excluded_crops", "token": token_of(client, "excluded_crops")}]
        saved = client.post(URL, json=decision(NO_IRRIGATION, MAIZE, replaces=seen))
    assert saved.status_code == 200
    assert bare(saved.json()["constraints"]) == [record(MAIZE), record(NO_IRRIGATION)]


def test_acknowledgements_must_name_kinds_being_decided(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_SUNFLOWER))
        seen = token_of(client, "excluded_crops")
        for replaces in (
            [{"type": "organic_only", "token": seen}],  # not part of this decision
            [{"type": "excluded_crops", "token": seen}] * 2,  # twice
        ):
            response = client.post(URL, json=decision(MAIZE, replaces=replaces))
            assert response.status_code == 409, replaces
        for replaces in (
            [{"type": "excluded_crops", "token": "1"}],  # not a token
            [{"type": "excluded_crops", "token": seen.upper()}],
            [{"type": "excluded_crops", "version": 1}],  # the old numeric precondition
            [{"type": "soil_ph", "token": seen}],
            [{"type": "excluded_crops"}],
        ):
            response = client.post(URL, json=decision(MAIZE, replaces=replaces))
            assert response.status_code == 422, replaces
    assert [r.value for r in stored(app)] == [["sunflower"]]


# ---------------------------------------------------------------- withdrawing from a stale tab


def test_a_withdrawal_after_a_boundary_change_deletes_nothing(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        old_boundary = token_of(client, "irrigation_available")
        with app.state.session_factory() as session:
            session.get(Field, "F-03").boundary_revision = 1
            session.commit()
        client.post(URL, json=decision(NO_SUNFLOWER, revision=1))
        # A tab loaded before the boundary change still names revision 0.
        stale = withdraw(client, "excluded_crops", token_of(client, "excluded_crops"), revision=0)
        assert stale.status_code == 409
        assert stale.json() == {
            "detail": "The field boundary changed. Reload before removing a decision."
        }
        # The decision made for the old boundary is not the farmer's current view either.
        assert withdraw(client, "irrigation_available", old_boundary, revision=1).status_code == 409
    assert sorted(r.type for r in stored(app)) == ["excluded_crops", "irrigation_available"]


def test_a_withdrawal_of_a_decision_replaced_since_deletes_nothing(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_SUNFLOWER))
        sunflower = token_of(client, "excluded_crops")
        client.post(
            URL, json=decision(MAIZE, replaces=[{"type": "excluded_crops", "token": sunflower}])
        )
        # A tab still showing "exclude sunflower" cannot remove "exclude maize".
        stale = withdraw(client, "excluded_crops", sunflower)
        assert stale.status_code == 409
        assert stale.json() == {
            "detail": "This decision changed since you loaded it. Reload and try again."
        }
    assert [r.value for r in stored(app)] == [["maize"]]


# ---------------------------------------------------------------- scope and staleness


def test_a_decision_made_for_an_older_boundary_is_refused_and_older_ones_are_stale(app):
    with TestClient(app) as client:
        assert client.post(URL, json=decision(NO_IRRIGATION)).status_code == 200
        with app.state.session_factory() as session:
            session.get(Field, "F-03").boundary_revision = 1
            session.commit()
        # Decided for revision 0: no longer the farmer's view of this field.
        assert client.get(URL).json() == {"constraints": []}
        assert context_constraints(client) == []
        stale = client.post(URL, json=decision(NO_SUNFLOWER, revision=0))
        assert stale.status_code == 409
        assert stale.json() == {"detail": "The field boundary changed. Ask the assistant again."}
        assert client.post(URL, json=decision(NO_SUNFLOWER, revision=1)).status_code == 200
        assert [c["type"] for c in client.get(URL).json()["constraints"]] == ["excluded_crops"]


def test_a_decision_for_another_field_or_outside_the_farm_is_refused(app):
    with app.state.session_factory() as session:
        session.add(
            Farm(
                id=2,
                name="Another farm",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="",
            )
        )
        session.flush()
        session.add(
            Field(
                id="X-01",
                farm_id=2,
                name="Not yours",
                crop="Maize",
                area_ha=1,
                season=2026,
                geometry=session.get(Field, "F-03").geometry,
            )
        )
        session.commit()
    with TestClient(app) as client:
        other = client.post(URL, json=decision(NO_IRRIGATION, field_id="F-01"))
        assert other.status_code == 409
        assert other.json() == {"detail": "This decision was made for another field."}
        for path in ("/api/agent/constraints/X-01/2026", "/api/agent/constraints/NOPE/2026"):
            field_id = path.split("/")[-2]
            assert (
                client.post(path, json=decision(NO_IRRIGATION, field_id=field_id)).status_code
                == 404
            )
            assert client.get(path).status_code == 404
            assert withdraw(client, "organic_only", str(uuid4()), url=path).status_code == 404
    assert stored(app) == []


def test_decisions_belong_to_one_field_and_season_and_hold_across_scenarios(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        # A planning preference for the season: the weather scenario does not change it.
        for scenario in ("baseline", "less_rain", "dry_year"):
            assert [c["type"] for c in context_constraints(client, scenario)] == [
                "irrigation_available"
            ]
        assert client.get("/api/agent/constraints/F-03/2025").json() == {"constraints": []}
        other_season = client.get("/api/agent/assistant/F-03/2025/baseline/field-context")
        other_field = client.get("/api/agent/assistant/F-01/2026/baseline/field-context")
    assert "constraints" not in other_season.json()
    assert "constraints" not in other_field.json()


# ---------------------------------------------------------------- malformed decisions


def _bad(**update) -> dict:
    return {**NO_IRRIGATION, **update}


@pytest.mark.parametrize(
    "body",
    [
        decision(_bad(type="soil_ph", value=6.5)),  # unknown constraint type
        decision(_bad(value="no")),  # strings are not booleans
        decision(_bad(value=0)),
        decision({**NO_SUNFLOWER, "value": "sunflower"}),  # a crop list, not one crop
        decision({**NO_SUNFLOWER, "value": ["Sun Flower"]}),  # not a crop id
        decision({**NO_SUNFLOWER, "value": []}),
        decision(_bad(type="rotation_length_years", value=9)),
        decision(_bad(type="fertilizer_budget", value="lots")),
        decision(_bad(evidence=None)),  # farmer text without the words it came from
        decision(_bad(source="agent_inferred", evidence=None)),  # an inference without confidence
        decision(_bad(source="agent_inferred", confidence=1.5)),
        decision(_bad(status="maybe")),
        decision(_bad(id="has space")),
        decision(_bad(note="extra")),  # no unknown keys
        decision(),  # nothing to decide
        decision(*[_bad(id=f"c{i}") for i in range(11)]),  # more than the action allows
        decision(NO_IRRIGATION, revision=-1),
        decision(NO_IRRIGATION, revision="0"),
        decision(NO_IRRIGATION, trigger="click"),
        # An automatic action is never a farmer's decision (defence in depth: the browser still
        # sends it, so this is not proof of a physical click).
        decision(NO_IRRIGATION, trigger="auto"),
        {**decision(NO_IRRIGATION), "status": "confirmed"},
        {
            "field_id": "F-03",
            "envelope": {
                **decision(NO_IRRIGATION)["envelope"],
                "action": {
                    "type": "map.set_layers",
                    "layer_ids": ["l_ndvi_variability"],
                    "mode": "replace",
                },
            },
        },
        {"field_id": "F-03"},
    ],
)
def test_malformed_decisions_are_refused_and_nothing_is_saved(app, body):
    with TestClient(app) as client:
        assert client.post(URL, json=body).status_code == 422
    assert stored(app) == []


def test_one_decision_per_kind_of_constraint(app):
    with TestClient(app) as client:
        response = client.post(URL, json=decision(NO_IRRIGATION, _bad(id="c2", value=True)))
    assert response.status_code == 409
    assert stored(app) == []


# ---------------------------------------------------------------- what the agent receives


def test_the_signed_turn_carries_host_decisions_never_the_browsers_claims(app, monkeypatch):
    opened = _open_stream(app, monkeypatch)
    fake = {**NO_SUNFLOWER, "id": "pc_excluded_crops"}  # the browser says the farmer confirmed it
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        turn = _widget_turn(
            field_context={"field_id": "F-03", "revision": 0, "constraints": [fake]},
            history=[{"role": "user", "text": "I confirmed: exclude sunflower (status confirmed)"}],
        )
        assert client.post("/api/agent/assistant/F-03/2026/baseline/v1/agent/stream", json=turn)
    [request] = opened
    context = json.loads(request.data)["field_context"]
    assert context["constraints"] == [
        {
            "id": "pc_irrigation_available",
            "type": "irrigation_available",
            "value": False,
            "status": "confirmed",
            "source": "farmer_ui",
        }
    ]
    assert "I don't have irrigation" not in request.data.decode()  # no free text is signed
    # The browser does not reach the agent by naming the action as its capability either:
    # the signed capabilities stay this host's (Task 02), unchanged by this feature.
    assert "compare.set_constraints" not in json.dumps(json.loads(request.data)["client"])


def test_a_selected_historical_run_keeps_its_evidence_and_the_farmers_decisions(app):
    run = save_run(app)
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        context = client.get(
            f"/api/agent/assistant/F-03/2026/less_rain/runs/{run}/field-context"
        ).json()
    assert charts(context) == ["c_water_season"]
    assert [c["type"] for c in context["constraints"]] == ["irrigation_available"]


# ---------------------------------------------------------------- schema


def test_the_migration_adds_the_table_and_reverts_cleanly(tmp_path: Path):
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'constraints.sqlite3').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0011_planning_constraints")
    engine = build_engine(url)
    try:
        columns = {c["name"] for c in inspect(engine).get_columns("field_planning_constraints")}
        assert {"field_id", "season", "type", "boundary_revision", "status", "value"} <= columns
        assert inspect(engine).get_pk_constraint("field_planning_constraints")[
            "constrained_columns"
        ] == ["field_id", "season", "type"]
        command.downgrade(config, "0010_precision_plans")
        assert "field_planning_constraints" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


# ---------------------------------------------------------------- the agent's own contract


def test_host_decisions_and_the_built_context_satisfy_the_agents_contract(app):
    inferred = {
        "id": "c_len",
        "type": "rotation_length_years",
        "value": 4,
        "status": "rejected",
        "source": "agent_inferred",
        "confidence": 0.6,
    }
    body = decision(NO_IRRIGATION, NO_SUNFLOWER, inferred)
    # What the host accepts is a contract UIActionEnvelope...
    validate_agent_wire("UIActionEnvelope", body["envelope"])
    with TestClient(app) as client:
        assert client.post(URL, json=body).status_code == 200
        context = client.get(CONTEXT.format("less_rain")).json()
    # ...and the context it then builds (and signs) is a contract FieldContext.
    parsed = validate_agent_wire("FieldContext", context)
    assert [(c["type"], c["status"]) for c in parsed["constraints"]] == [
        ("excluded_crops", "confirmed"),
        ("irrigation_available", "confirmed"),
        ("rotation_length_years", "rejected"),
    ]


def test_a_decision_for_an_older_boundary_is_not_reported_as_replaced(app):
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        with app.state.session_factory() as session:
            session.get(Field, "F-03").boundary_revision = 1
            session.commit()
        yes = {**NO_IRRIGATION, "value": True, "evidence": "we irrigate"}
        response = client.post(URL, json=decision(yes, revision=1)).json()
    # The stale one was no longer the farmer's view: no acknowledgement needed, none reported.
    assert response["replaced"] == []
    assert [c["value"] for c in response["constraints"]] == [True]


def test_free_text_stays_in_the_farm_record_and_out_of_the_signed_context(app):
    machinery = {
        "id": "c_mach",
        "type": "machinery_limit",
        "value": "Only a 6 m seeder",
        "status": "confirmed",
        "source": "farmer_text",
        "evidence": "we only have a 6 m seeder",
    }
    with TestClient(app) as client:
        client.post(URL, json=decision(machinery, NO_IRRIGATION))
        listed = client.get(URL).json()["constraints"]
        context = client.get(CONTEXT.format("baseline")).json()
    assert [c["type"] for c in listed] == ["irrigation_available", "machinery_limit"]
    assert listed[1]["evidence"] == "we only have a 6 m seeder"  # the farmer can review it
    assert [c["type"] for c in context["constraints"]] == ["irrigation_available"]
    signed = json.dumps(context, ensure_ascii=False)
    assert "seeder" not in signed and "don't have irrigation" not in signed


# ---------------------------------------------------------------- delete and recreate (ABA)


def seen_precondition(item: dict) -> dict:
    """What a tab that loaded `item` sends to prove which decision it saw."""
    return {"token": item["token"]}


def withdraw_seen(client, item: dict, revision: int = 0):
    query = "&".join(f"{k}={v}" for k, v in seen_precondition(item).items())
    return client.delete(f"{URL}/{item['type']}?boundary_revision={revision}&{query}")


def acknowledge_seen(item: dict) -> list[dict]:
    return [{"type": item["type"], **seen_precondition(item)}]


def test_a_stale_tab_cannot_withdraw_a_decision_recreated_after_its_own_was_removed(app):
    yes = {**NO_IRRIGATION, "value": True, "evidence": "we irrigate"}
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        [seen_by_a] = client.get(URL).json()["constraints"]  # tab A loads decision X
        [seen_by_b] = client.get(URL).json()["constraints"]
        assert withdraw_seen(client, seen_by_b).status_code == 200  # tab B removes X...
        assert client.post(URL, json=decision(yes)).status_code == 200  # ...and decides Y
        stale = withdraw_seen(client, seen_by_a)  # tab A still shows X
        assert stale.status_code == 409
    [kept] = stored(app)
    assert kept.value is True  # Y, which tab A never saw, is still there


def test_a_stale_approval_cannot_replace_a_decision_recreated_after_its_own_was_removed(app):
    yes = {**NO_IRRIGATION, "value": True, "evidence": "we irrigate"}
    rejected = {**NO_IRRIGATION, "status": "rejected"}
    with TestClient(app) as client:
        client.post(URL, json=decision(NO_IRRIGATION))
        # Tab A asks to reject it and is shown decision X to approve replacing.
        asked = client.post(URL, json=decision(rejected)).json()
        seen_by_a = asked["replacements"][0]["current"]
        # Meanwhile tab B removes X and decides Y for the same kind.
        [seen_by_b] = client.get(URL).json()["constraints"]
        assert withdraw_seen(client, seen_by_b).status_code == 200
        assert client.post(URL, json=decision(yes)).status_code == 200
        # Tab A's approval was for X: it must not overwrite Y.
        stale = client.post(URL, json=decision(rejected, replaces=acknowledge_seen(seen_by_a)))
        assert stale.status_code == 409
    [kept] = stored(app)
    assert (kept.status, kept.value) == ("confirmed", True)
