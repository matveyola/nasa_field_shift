from field_shift.contracts.planning_v1.common import EvidenceRef, Reason


def reason(code, message, locator="planning-v1:spatial-v1", version="spatial-v1", kind="rule"):
    return Reason(
        code=code,
        message=message,
        evidence=[
            EvidenceRef(
                id=code,
                kind=kind,
                version=version,
                locator=locator,
            )
        ],
    )
