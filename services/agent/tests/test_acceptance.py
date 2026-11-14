"""M6C: the canonical Poltava acceptance scenario passes every check (agent + adapters against the contract stub)."""

from evals.acceptance import run


def test_canonical_poltava_scenario_passes_every_check():
    ev = run()
    failed = [(c.name, c.observed) for c in ev.checks if c.outcome != "PASS"]
    assert not failed
    assert len(ev.checks) == 25
    assert "NOT a teammate service" in ev.target  # the evidence never claims a real integration
