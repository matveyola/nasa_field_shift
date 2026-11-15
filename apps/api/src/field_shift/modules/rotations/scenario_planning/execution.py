"""Injected A/B orchestration, without a mock provider or shared worker registration."""

from time import monotonic

from .common import digest
from .selector import BoundedSelector, Cancelled


class PlanningEngine:
    def __init__(
        self, profiles, suitability, crop_model, *, clock=monotonic, cancelled=lambda: False
    ):
        self.profiles = profiles
        self.suitability = suitability
        self.crop_model = crop_model
        self.clock = clock
        self.cancelled = cancelled

    def execute(self, request):
        started = self.clock()

        def check():
            if self.cancelled():
                raise Cancelled()
            if self.clock() - started >= request.solver_limits.max_seconds:
                raise TimeoutError("planning execution deadline")

        check()
        for f in request.fields:
            trusted = self.profiles.get_snapshot(request.farm_id, f.field_id, f.snapshot_id)
            if trusted != f:
                raise ValueError("profile snapshot mismatch")
        candidates = self.suitability.candidates(request)
        if len(candidates) > 3000:
            raise ValueError("candidate admission limit")
        card = self.crop_model.model_card()
        if card.gate_frozen_at > request.as_of or (
            card.holdout_opened_at is not None and card.holdout_opened_at > request.as_of
        ):
            raise ValueError("model release evidence was unavailable at issue time")
        evaluations = []
        fields = {f.snapshot_id: f for f in request.fields}
        for c in candidates:
            check()
            # Ineligible candidates are retained for diagnostics, never sent to an engine.
            if any(a.status != "eligible" or a.unchecked_rule_ids for a in c.assessments):
                continue
            if c.field_snapshot_id not in fields:
                raise ValueError("candidate outside request snapshots")
            if any(a.crop not in card.supported_crops for a in c.assignments):
                continue
            required = {cap for a in c.assessments for cap in a.required_capabilities}
            if not required <= set(card.capabilities):
                continue
            evaluation = self.crop_model.evaluate(fields[c.field_snapshot_id], c, request.weather)
            if evaluation.model != card.model:
                raise ValueError("evaluation model differs from pinned model card")
            if evaluation.applicability == "validated" and card.promotion != "passed":
                raise ValueError("unpromoted model cannot claim validated applicability")
            evaluations.append(evaluation)
        check()
        remaining = max(1, int(request.solver_limits.max_seconds - (self.clock() - started)))
        # Keep the original immutable request/hash while sharing the full-execution deadline.
        selector = BoundedSelector(clock=self.clock, cancelled=lambda: self.cancelled() or False)
        selector_request = request.model_copy(
            update={
                "solver_limits": request.solver_limits.model_copy(update={"max_seconds": remaining})
            }
        )
        comparison = selector.compare(selector_request, candidates, evaluations)
        comparison = comparison.model_copy(
            update={
                "input_sha256": digest(request),
                "comparison_id": "comparison-" + digest(request)[:24],
                "elapsed_seconds": self.clock() - started,
            }
        )
        artifacts = {
            "model_card": card.model_dump(mode="json"),
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "evaluations": [e.model_dump(mode="json") for e in evaluations],
        }
        artifacts["sha256"] = digest(artifacts)
        return comparison, artifacts
