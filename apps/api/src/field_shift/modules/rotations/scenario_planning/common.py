"""Canonical snapshot hashing and evidence, with no provider defaults."""

import hashlib
import json

from field_shift.contracts.planning_v1.common import EvidenceRef, Reason


def canonical(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def reason(code, message, ref, kind="constraint", version="1"):
    return Reason(
        code=code,
        message=message,
        evidence=[EvidenceRef(id=ref, kind=kind, version=version, locator=ref)],
    )
