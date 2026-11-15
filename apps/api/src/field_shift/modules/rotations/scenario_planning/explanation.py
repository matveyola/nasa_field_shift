"""Deterministic facts; optional provider can only select existing fact references.

Free prose cannot be checked for scientific fidelity. The ExplanationProvider's
string must therefore be JSON {"references": [fact IDs]}. All rendered prose is
owned here; unsupported text, numbers, units or new assertions trigger fallback.
"""

import json

from .common import canonical, digest


def evidence_pack(comparison, alternative_id, *, evaluations=()):
    plan = next(p for p in comparison.alternatives if p.alternative_id == alternative_id)
    facts = {
        "policy": "Scenario gross margin is not net profit or a guaranteed return. "
        "Tested trajectories do not imply calibrated probabilities."
    }
    for i, result in enumerate(plan.scenario_results):
        facts[f"result-{i}"] = canonical(result)
    for evaluation in evaluations:
        if evaluation.evaluation_id in plan.evaluation_ids:
            facts["evaluation-" + evaluation.evaluation_id] = canonical(evaluation)
    facts["objective"] = canonical({"value": plan.objective_value, "unit": plan.objective_unit})
    for i, r in enumerate([*plan.reasons, *comparison.diagnostics]):
        facts[f"reason-{i}"] = canonical(r)
    return {
        "comparison_id": comparison.comparison_id,
        "alternative_id": alternative_id,
        "input_sha256": comparison.input_sha256,
        "facts": facts,
        "sha256": digest(facts),
    }


def explain(comparison, alternative_id, provider=None, *, evaluations=()):
    pack = evidence_pack(comparison, alternative_id, evaluations=evaluations)
    refs = list(pack["facts"])
    if provider:
        try:
            # Provider receives a deep copy: a malicious/buggy adapter cannot mutate results.
            output = json.loads(provider.explain(comparison.model_copy(deep=True), alternative_id))
            if (
                set(output) != {"references"}
                or not isinstance(output["references"], list)
                or set(output["references"]) != set(refs)
                or len(output["references"]) != len(refs)
            ):
                raise ValueError("explanation must preserve every fact exactly")
            refs = output["references"]
        except Exception:
            pass  # The same deterministic evidence remains available on provider failure.
    return "\n".join(f"[{ref}] {pack['facts'][ref]}" for ref in refs)
