import { describe, expect, it } from "vitest";
import { poltava } from "../../test/helpers";
import type { UIActionEnvelope } from "../contracts";
import { actionProblem, confirmationEnvelope, type HostCapabilities, isCurrent, type KnownResult, requiresClick } from "./policy";

const caps: HostCapabilities = { actions: ["map.set_layers", "compare.set_constraints", "scenario.set", "chart.focus"], simulation: false };
const env = (action: UIActionEnvelope["action"], over: Partial<UIActionEnvelope> = {}): UIActionEnvelope => ({
  id: "a1",
  field_context_revision: 3,
  trigger: "chip",
  label: "Layer",
  action,
  ...over,
});
const layers = (ids: string[]) => env({ type: "map.set_layers", layer_ids: ids, mode: "replace" });

describe("actionProblem (host-side check)", () => {
  const ctx = poltava(); // revision 3

  it("accepts a known layer for the current field and revision", () => {
    expect(actionProblem(layers(["l_smap_rootzone"]), ctx, caps, ctx.field_id)).toBeNull();
  });

  it("rejects actions for another field, a stale revision, or no field", () => {
    expect(actionProblem(layers(["l_smap_rootzone"]), ctx, caps, "demo-kansas-01")).toBe("field_changed");
    expect(actionProblem({ ...layers(["l_smap_rootzone"]), field_context_revision: 2 }, ctx, caps, ctx.field_id)).toBe("stale_context");
    expect(actionProblem(layers(["l_smap_rootzone"]), null, caps, ctx.field_id)).toBe("field_changed");
  });

  it("rejects ids the host does not have and modules it lacks", () => {
    expect(actionProblem(layers(["l_made_up"]), ctx, caps, ctx.field_id)).toBe("unknown_reference");
    expect(actionProblem(env({ type: "chart.focus", chart_id: "c_water_season", metric_ids: ["m_ndvi"] }), ctx, caps, ctx.field_id)).toBe(
      "unknown_reference",
    );
    expect(actionProblem(env({ type: "map.focus_field" }), ctx, caps, ctx.field_id)).toBe("module_unavailable");
    const withRotations: HostCapabilities = { ...caps, actions: [...caps.actions, "compare.select_rotations"] };
    expect(actionProblem(env({ type: "compare.select_rotations", rotation_ids: ["r1"] }), ctx, withRotations, ctx.field_id)).toBe("module_unavailable");
  });

  it("refuses to persist constraints the farmer has not confirmed", () => {
    const proposed = { id: "k1", type: "irrigation_available" as const, value: false, status: "proposed" as const, source: "farmer_text" as const, evidence: "no irrigation" };
    expect(actionProblem(env({ type: "compare.set_constraints", constraints: [proposed] }), ctx, caps, ctx.field_id)).toBe("not_permitted");
    expect(actionProblem(env({ type: "compare.set_constraints", constraints: [{ ...proposed, status: "confirmed" }] }), ctx, caps, ctx.field_id)).toBeNull();
  });

  it("rejects anything that is not a contract-valid envelope", () => {
    expect(actionProblem({ id: "x", type: "eval", code: "alert(1)" }, ctx, caps, ctx.field_id)).toBe("invalid");
    expect(actionProblem({ ...layers(["l_smap_rootzone"]), label: undefined }, ctx, caps, ctx.field_id)).toBe("invalid");
  });
});

describe("simulation result references", () => {
  const ctx = poltava();
  const simCaps: HostCapabilities = { actions: ["panel.open", "compare.select_rotations"], simulation: true };
  const now = new Date("2026-11-06T12:00:00Z");
  const result = (over: Partial<KnownResult> = {}): KnownResult => ({
    result_id: "cmp_real", field_id: ctx.field_id, expires_at: "2026-11-06T13:00:00Z", rotation_ids: ["rot_adaptive", "rot_conventional"], ...over,
  });
  const panel = (ref: string) => env({ type: "panel.open", panel: "comparison", ref_id: ref });
  const select = (ids: string[]) => env({ type: "compare.select_rotations", rotation_ids: ids });
  const known = (r: KnownResult) => new Map([[r.result_id, r]]);

  it("opens the comparison only for a result the host fetched for this field and that has not expired", () => {
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, known(result()), now)).toBeNull();
    expect(actionProblem(panel("cmp_invented"), ctx, simCaps, ctx.field_id, known(result()), now)).toBe("unknown_reference");
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, known(result({ field_id: "demo-kansas-01" })), now)).toBe("field_changed");
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, known(result({ expires_at: "2026-11-06T11:59:00Z" })), now)).toBe("expired");
  });

  it("refuses a valid result for another scenario than the one the agent named (M6C)", () => {
    const drought = env({ type: "panel.open", panel: "comparison", ref_id: "cmp_real", scenario: "drought_2024" });
    const typical = env({ type: "panel.open", panel: "comparison", ref_id: "cmp_real", scenario: "typical" });
    expect(actionProblem(drought, ctx, simCaps, ctx.field_id, known(result({ scenario: "typical" })), now)).toBe("unknown_reference");
    expect(actionProblem(drought, ctx, simCaps, ctx.field_id, known(result()), now)).toBe("unknown_reference"); // unknown scenario: fail closed
    expect(actionProblem(typical, ctx, simCaps, ctx.field_id, known(result({ scenario: "typical" })), now)).toBeNull();
  });

  it("refuses a result computed for a revision this host has not reached (replay / foreign context)", () => {
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, known(result({ field_context_revision: 9 })), now)).toBe("stale_context");
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, known(result({ field_context_revision: 3 })), now)).toBeNull();
    expect(actionProblem(select(["rot_adaptive"]), ctx, simCaps, ctx.field_id, known(result({ field_context_revision: 9 })), now)).toBe("unknown_reference");
  });

  it("a cached result that expires later is refused from then on", () => {
    const cached = known(result());
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, cached, now)).toBeNull();
    expect(actionProblem(panel("cmp_real"), ctx, simCaps, ctx.field_id, cached, new Date("2026-11-06T13:00:01Z"))).toBe("expired");
  });

  it("selects only rotations that a live result contains", () => {
    expect(actionProblem(select(["rot_adaptive"]), ctx, simCaps, ctx.field_id, known(result()), now)).toBeNull();
    expect(actionProblem(select(["rot_magic"]), ctx, simCaps, ctx.field_id, known(result()), now)).toBe("unknown_reference");
    expect(actionProblem(select(["rot_adaptive"]), ctx, simCaps, ctx.field_id, new Map(), now)).toBe("unknown_reference");
    expect(actionProblem(select(["rot_adaptive"]), ctx, { ...simCaps, simulation: false }, ctx.field_id, known(result()), now)).toBe("module_unavailable");
  });

  it("other panels need no result", () => {
    expect(actionProblem(env({ type: "panel.open", panel: "citations" }), ctx, simCaps, ctx.field_id)).toBeNull();
  });
});

describe("confirmation rules", () => {
  it("never auto-applies preference changes, whatever the trigger says", () => {
    expect(requiresClick(env({ type: "compare.set_constraints", constraints: [] }, { trigger: "auto" }))).toBe(true);
    expect(requiresClick(env({ type: "scenario.set", scenario: "drought_2024" }, { trigger: "auto" }))).toBe(true);
    expect(requiresClick(layers(["l_smap_rootzone"]))).toBe(true);
    expect(requiresClick({ ...layers(["l_smap_rootzone"]), trigger: "auto" })).toBe(false);
  });

  it("treats a message's own revision chain as current, and nothing else", () => {
    const ctx = poltava();
    expect(isCurrent(ctx, ctx.field_id, [3])).toBe(true);
    expect(isCurrent({ ...ctx, revision: 4 }, ctx.field_id, [3])).toBe(false);
    expect(isCurrent({ ...ctx, revision: 4 }, ctx.field_id, [3, 4])).toBe(true);
    expect(isCurrent({ ...ctx, field_id: "other" }, ctx.field_id, [3])).toBe(false);
  });

  it("builds a confirmation as a confirmed constraint stamped with the current revision", () => {
    const ctx = { ...poltava(), revision: 9 };
    const c = { id: "k1", type: "fertilizer_budget" as const, value: "low" as const, status: "proposed" as const, source: "farmer_text" as const, evidence: "less fertiliser" };
    const e = confirmationEnvelope(c, ctx, "Low fertiliser budget");
    expect(e.field_context_revision).toBe(9);
    expect(e.action).toEqual({ type: "compare.set_constraints", constraints: [{ ...c, status: "confirmed" }] });
    expect(actionProblem(e, ctx, caps, ctx.field_id)).toBeNull();
  });
});
