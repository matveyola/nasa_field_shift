import json

from agent_service.attestation import HEADER, sign
from agent_service.config import Settings

from .conftest import context, make_client, request_body


def test_signed_host_turn_reads_saved_values_and_proposes_only_available_views():
    ctx = context("poltava")
    period = {"start": "2026-06-12", "end": "2026-06-16"}
    provenance = {
        "kind": "deterministic_calc",
        "data_status": "cached",
        "method": "Stored model calculation for this test field",
        "temporal": period,
        "retrieved_at": "2026-06-12T06:00:00Z",
    }
    ctx.update(
        facts=[
            {"id": "f_forecast_min_root_zone_reserve", "domain": "water",
             "label_key": "fact.forecast_min_root_zone_reserve", "quantity": {"value": 42.5, "unit": "mm"},
             "provenance": provenance},
            {"id": "f_ndvi_latest", "domain": "vegetation", "label_key": "fact.ndvi_latest",
             "quantity": {"value": 0.67, "unit": "1"}, "provenance": provenance},
        ],
        timeseries_index=[
            {"metric_id": "m_rootzone_reserve", "label_key": "fact.forecast_root_zone_reserve",
             "unit": "mm", "range": period, "provenance": provenance},
            {"metric_id": "m_ndvi", "label_key": "fact.ndvi_latest", "unit": "1", "range": period,
             "provenance": provenance},
        ],
        available_charts=[
            {"chart_id": "c_water_forecast", "metric_ids": ["m_rootzone_reserve"]},
            {"chart_id": "c_vegetation", "metric_ids": ["m_ndvi"]},
        ],
        available_layers=[
            {"layer_id": "l_ndvi_variability", "label_key": "layer.ndvi_variability", "kind": "raster",
             "provenance": provenance},
        ],
        ui_state={},
    )
    key, secret = "a" * 40, "b" * 40
    settings = Settings(environment="production", cors_origins=[], agent="scripted-llm", rag_mode="off",
                        api_key=key, context_signing_key=secret, require_context_attestation=True)
    with make_client(settings) as client:
        raw = json.dumps(request_body(ctx, "Покажи прогноз води та NDVI.", locale="uk")).encode()
        token = sign(secret.encode(), principal="test-farm", field_id=ctx["field_id"],
                     revision=ctx["revision"], body=raw)
        response = client.post("/v1/agent/message", content=raw, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}", HEADER: token,
        })
        assert response.status_code == 200, response.text
        message = response.json()
        assert client.app.state.service.agent.provider.requests == []

    assert message["meta"]["provider"] == "scripted-llm"
    assert {(fact["label_key"], fact["quantity"]["unit"]) for fact in message["facts"]} == {
        ("fact.forecast_min_root_zone_reserve", "mm"), ("fact.ndvi_latest", "1"),
    }
    assert all(action["trigger"] == "chip" for action in message["ui_actions"])
    assert {action["action"]["chart_id"] for action in message["ui_actions"]
            if action["action"]["type"] == "chart.focus"} == {"c_water_forecast", "c_vegetation"}
    assert [action["action"]["layer_ids"] for action in message["ui_actions"]
            if action["action"]["type"] == "map.set_layers"] == [["l_ndvi_variability"]]
