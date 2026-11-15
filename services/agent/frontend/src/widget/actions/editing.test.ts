import { describe, expect, it } from "vitest";
import type { Constraint } from "../contracts";
import { canEdit, editBlock, editConstraint, editorKind, unsupportedCrops } from "./editing";

// The host's crops whose identity the agent shares (apps/web constraintCrops.ts).
const crops = [
  { id: "maize", label: "Maize" },
  { id: "soybean", label: "Soy" },
  { id: "sunflower", label: "Sunflower" },
];
const said = { status: "proposed", source: "farmer_text", evidence: "Поливу немає", confidence: 0.97 } as const;
const c = (type: Constraint["type"], value: unknown, extra: object = said) => ({ id: "k1", type, value, ...extra }) as Constraint;

describe("editing a proposed constraint", () => {
  it("picks the editor the contract type needs", () => {
    expect(editorKind("irrigation_available")).toBe("boolean");
    expect(editorKind("organic_only")).toBe("boolean");
    expect(editorKind("fertilizer_budget")).toBe("fertilizer");
    expect(editorKind("rotation_length_years")).toBe("years");
    expect(editorKind("excluded_crops")).toBe("crops");
    expect(editorKind("market_preference")).toBe("crops");
    expect(editorKind("previous_crop")).toBe("crop");
    expect(editorKind("machinery_limit")).toBe("text");
  });

  it("makes a changed value the farmer's own choice in the app, with no quoted words or model confidence", () => {
    const result = editConstraint(c("irrigation_available", false), true, crops);
    expect(result).toEqual({
      ok: true,
      edited: true,
      constraint: { id: "k1", type: "irrigation_available", value: true, status: "proposed", source: "farmer_ui" },
    });
  });

  it("keeps an unchanged proposal exactly as proposed, with its source and evidence", () => {
    const original = c("irrigation_available", false);
    expect(editConstraint(original, false, crops)).toEqual({ ok: true, edited: false, constraint: original });
  });

  it("an edited value is still a proposal: only the farmer's Confirm or Reject decides it", () => {
    const result = editConstraint(c("fertilizer_budget", "minimize_n"), "low", crops);
    expect(result.ok && result.constraint.status).toBe("proposed");
  });

  it("accepts only the contract's values for each type", () => {
    expect(editConstraint(c("organic_only", true), "yes", crops)).toEqual({ ok: false, error: "invalid" });
    expect(editConstraint(c("fertilizer_budget", "low"), "lots", crops)).toEqual({ ok: false, error: "invalid" });
    for (const years of [1, 7, 2.5, "", "abc", "3.5", null]) {
      expect(editConstraint(c("rotation_length_years", 4), years, crops), String(years)).toEqual({ ok: false, error: "rotation_range" });
    }
    expect(editConstraint(c("rotation_length_years", 4), "5", crops)).toMatchObject({ ok: true, constraint: { value: 5 } });
    expect(editConstraint(c("rotation_length_years", 4), 2, crops)).toMatchObject({ ok: true, constraint: { value: 2 } });
    expect(editConstraint(c("rotation_length_years", 4), 6, crops)).toMatchObject({ ok: true, constraint: { value: 6 } });
  });

  it("chooses crops only from the host's crop list, never substituting one", () => {
    const proposal = c("excluded_crops", ["winter_wheat", "sunflower"]);
    expect(unsupportedCrops(proposal, crops)).toEqual(["winter_wheat"]);
    expect(editConstraint(proposal, ["winter_wheat"], crops)).toEqual({ ok: false, error: "crop_unsupported" });
    expect(editConstraint(proposal, [], crops)).toEqual({ ok: false, error: "crops_required" });
    expect(editConstraint(proposal, ["maize", "sunflower", "maize"], crops)).toMatchObject({
      ok: true,
      constraint: { value: ["maize", "sunflower"], source: "farmer_ui" },
    });
    // Not the same crop under another name: generic wheat or peas are not the agent's winter crops.
    expect(editConstraint(proposal, ["wheat"], crops)).toEqual({ ok: false, error: "crop_unsupported" });
    expect(editConstraint(proposal, ["peas"], crops)).toEqual({ ok: false, error: "crop_unsupported" });
    expect(editConstraint(c("previous_crop", "winter_wheat"), "", crops)).toEqual({ ok: false, error: "crops_required" });
    expect(editConstraint(c("previous_crop", "winter_wheat"), "barley", crops)).toEqual({ ok: false, error: "crop_unsupported" });
    expect(editConstraint(c("previous_crop", "winter_wheat"), "maize", crops)).toMatchObject({ ok: true, constraint: { value: "maize" } });
  });

  it("crops can be edited only with a host crop list; other types always can", () => {
    expect(editBlock(c("excluded_crops", ["maize"]), [])).toBe("no_crop_list");
    expect(canEdit(c("previous_crop", "maize"), [])).toBe(false);
    expect(canEdit(c("excluded_crops", ["maize"]), crops)).toBe(true);
    expect(canEdit(c("organic_only", true), [])).toBe(true);
  });

  it("a proposal naming a crop outside the shared list cannot be edited: its crops are never dropped or rewritten", () => {
    expect(editBlock(c("excluded_crops", ["winter_wheat", "sunflower"]), crops)).toBe("unshared_crops");
    expect(editBlock(c("preferred_crops", ["winter_pea"]), crops)).toBe("unshared_crops");
    expect(editBlock(c("market_preference", ["rapeseed"]), crops)).toBe("unshared_crops");
    expect(editBlock(c("previous_crop", "winter_wheat"), crops)).toBe("unshared_crops");
    expect(editBlock(c("excluded_crops", ["sunflower", "soybean"]), crops)).toBeNull();
  });

  it("keeps machinery restrictions to clean text within the contract's length", () => {
    const proposal = c("machinery_limit", "Only a 6 m seeder");
    expect(editConstraint(proposal, "   ", crops)).toEqual({ ok: false, error: "text_required" });
    expect(editConstraint(proposal, "x".repeat(301), crops)).toEqual({ ok: false, error: "text_too_long" });
    expect(editConstraint(proposal, "  Only a\n9 m seeder ", crops)).toMatchObject({ ok: true, constraint: { value: "Only a 9 m seeder" } });
    expect(editConstraint(proposal, "x".repeat(300), crops).ok).toBe(true);
  });

  it("an agent inference that the farmer changes is no longer marked as inferred", () => {
    const inferred = c("rotation_length_years", 4, { status: "proposed", source: "agent_inferred", confidence: 0.6 });
    const result = editConstraint(inferred, 5, crops);
    expect(result).toMatchObject({ ok: true, edited: true, constraint: { source: "farmer_ui" } });
    expect(result.ok && "confidence" in result.constraint).toBe(false);
  });
});
