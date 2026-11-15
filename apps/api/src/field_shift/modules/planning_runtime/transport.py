"""Bounded private HTTP; no crop/training packages in the public API image."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from field_shift_contracts.planning_v1 import ModelCard, SequenceEvaluation

from field_shift.modules.rotations.scenario_planning.common import canonical

MAX_BYTES = 16 * 1024 * 1024


class ModelUnavailable(RuntimeError):
    pass


class ModelClient:
    def call(self, operation, payload):
        url = os.getenv("FIELD_SHIFT_CROP_MODEL_URL", "").rstrip("/")
        token = os.getenv("FIELD_SHIFT_CROP_MODEL_TOKEN", "")
        if not url or len(token) < 32:
            raise ModelUnavailable("Private crop model is not configured")
        data = canonical(payload).encode()
        if len(data) > MAX_BYTES:
            raise ModelUnavailable("Private model input limit exceeded")
        request = Request(
            url + "/" + operation,
            data=data,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5 if operation == "card" else 65) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ModelUnavailable("Private model output limit exceeded")
            return json.loads(raw)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            raise ModelUnavailable("Private crop model unavailable; no substitute result") from exc

    def card(self, bundle):
        return ModelCard.model_validate_json(canonical(self.call("card", parameters(bundle))))

    def evaluate(self, bundle, profile, candidate, weather):
        payload = parameters(bundle) | {
            "profile": profile.model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json"),
            "weather": weather.model_dump(mode="json"),
        }
        return SequenceEvaluation.model_validate_json(canonical(self.call("evaluate", payload)))


def parameters(bundle):
    return {"hydraulics": bundle.hydraulics, "footprint": bundle.footprint}


class RemoteCropProvider:
    def __init__(self, bundle, expected_card, client=None):
        self.bundle, self.expected = bundle, expected_card
        self.client = client or ModelClient()

    def model_card(self):
        actual = self.client.card(self.bundle)
        if actual != self.expected:
            raise ModelUnavailable("Model artifact changed; review and reinstall input bundle")
        return actual

    def evaluate(self, profile, candidate, weather):
        result = self.client.evaluate(self.bundle, profile, candidate, weather)
        if result.model != self.expected.model:
            raise ModelUnavailable("Model response differs from pinned artifact")
        return result
