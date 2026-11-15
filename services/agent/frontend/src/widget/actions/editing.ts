import { isUIActionEnvelope } from "../contracts";
import type { Constraint } from "../contracts";

/** A crop the host app plans with, as a contract crop id and the farmer's name for it. */
export interface CropOption {
  id: string;
  label: string;
}

/** The editor a constraint type needs (CONTRACTS.md › Constraint). */
export type EditorKind = "boolean" | "fertilizer" | "years" | "crops" | "crop" | "text";

export type EditError =
  | "rotation_range" // rotation length outside 2–6 whole years
  | "crops_required" // a crop list needs at least one crop
  | "crop_unsupported" // a crop this app does not plan with
  | "text_required"
  | "text_too_long"
  | "invalid"; // anything else the contract refuses

export const ROTATION_YEARS = { min: 2, max: 6 } as const;
export const MAX_TEXT = 300;
export const FERTILIZER_BUDGETS = ["minimize_n", "low", "normal"] as const;

export function editorKind(type: Constraint["type"]): EditorKind {
  switch (type) {
    case "irrigation_available":
    case "erosion_protection":
    case "organic_only":
      return "boolean";
    case "fertilizer_budget":
      return "fertilizer";
    case "rotation_length_years":
      return "years";
    case "excluded_crops":
    case "preferred_crops":
    case "market_preference":
      return "crops";
    case "previous_crop":
      return "crop";
    case "machinery_limit":
      return "text";
  }
}

/**
 * Why a proposal cannot be edited here, or null. Crops can be edited only from the host's list of crops whose identity
 * it shares with the agent, and only if every crop the proposal names is on it: editing must never drop, swap or
 * rewrite a crop the assistant proposed. Such a proposal can still be confirmed, rejected or hidden as proposed.
 */
export function editBlock(c: Constraint, crops: readonly CropOption[]): "no_crop_list" | "unshared_crops" | null {
  const kind = editorKind(c.type);
  if (kind !== "crops" && kind !== "crop") return null;
  if (crops.length === 0) return "no_crop_list";
  return unsupportedCrops(c, crops).length > 0 ? "unshared_crops" : null;
}

export function canEdit(c: Constraint, crops: readonly CropOption[]): boolean {
  return editBlock(c, crops) === null;
}

/** The crops in a proposal that are not on the host's shared crop list: named to the farmer, never swapped or dropped. */
export function unsupportedCrops(c: Constraint, crops: readonly CropOption[]): string[] {
  const kind = editorKind(c.type);
  if (kind !== "crops" && kind !== "crop") return [];
  const known = new Set(crops.map((crop) => crop.id));
  const values = Array.isArray(c.value) ? c.value : [c.value];
  return values.filter((v): v is string => typeof v === "string" && !known.has(v));
}

export type EditResult = { ok: true; constraint: Constraint; edited: boolean } | { ok: false; error: EditError };

/**
 * The farmer's edit of a proposed constraint, checked before anything can be sent. An unchanged value keeps the
 * proposal as it was (its source and the farmer's quoted words). A changed value is the farmer's own choice in the
 * app: `farmer_ui`, with no quoted evidence and no model confidence, because the farmer did not say those words.
 * The result is still a proposal: only the farmer's Confirm or Reject decides it.
 */
export function editConstraint(original: Constraint, raw: unknown, crops: readonly CropOption[]): EditResult {
  const known = new Set(crops.map((crop) => crop.id));
  let value: unknown;
  switch (editorKind(original.type)) {
    case "boolean":
      if (typeof raw !== "boolean") return { ok: false, error: "invalid" };
      value = raw;
      break;
    case "fertilizer":
      if (!(FERTILIZER_BUDGETS as readonly unknown[]).includes(raw)) return { ok: false, error: "invalid" };
      value = raw;
      break;
    case "years": {
      const years = typeof raw === "string" && raw.trim() !== "" ? Number(raw) : raw;
      if (typeof years !== "number" || !Number.isInteger(years) || years < ROTATION_YEARS.min || years > ROTATION_YEARS.max) {
        return { ok: false, error: "rotation_range" };
      }
      value = years;
      break;
    }
    case "crops": {
      if (!Array.isArray(raw)) return { ok: false, error: "invalid" };
      const chosen = [...new Set(raw)];
      if (chosen.length === 0) return { ok: false, error: "crops_required" };
      if (!chosen.every((id) => typeof id === "string" && known.has(id))) return { ok: false, error: "crop_unsupported" };
      value = chosen;
      break;
    }
    case "crop":
      if (typeof raw !== "string" || raw === "") return { ok: false, error: "crops_required" };
      if (!known.has(raw)) return { ok: false, error: "crop_unsupported" };
      value = raw;
      break;
    case "text": {
      // eslint-disable-next-line no-control-regex
      const text = typeof raw === "string" ? raw.replace(/[\u0000-\u001f\u007f]+/g, " ").trim() : "";
      if (!text) return { ok: false, error: "text_required" };
      if (text.length > MAX_TEXT) return { ok: false, error: "text_too_long" };
      value = text;
      break;
    }
  }
  if (JSON.stringify(value) === JSON.stringify(original.value)) return { ok: true, constraint: original, edited: false };
  const edited = {
    id: original.id,
    type: original.type,
    value,
    status: "proposed",
    source: "farmer_ui",
  } as Constraint;
  // The contract's own validator is the last gate: nothing it refuses can reach the host.
  const probe = {
    id: "edit.check",
    field_context_revision: 0,
    trigger: "chip",
    label: "check",
    action: { type: "compare.set_constraints", constraints: [{ ...edited, status: "confirmed" }] },
  };
  return isUIActionEnvelope(probe) ? { ok: true, constraint: edited, edited: true } : { ok: false, error: "invalid" };
}
