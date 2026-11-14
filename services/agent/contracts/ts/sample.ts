// Compile-time check that the generated types are usable from TypeScript: `npm run check:ts`.
import type { AgentEvent, Constraint, FieldContext, UIActionEnvelope } from "./index";

const constraint: Constraint = {
  id: "k_irrigation",
  type: "irrigation_available",
  value: false,
  status: "confirmed",
  source: "farmer_text",
  evidence: "Поливу немає",
};

export const field: FieldContext = {
  schema_version: "0.1",
  field_id: "demo-sample-01",
  revision: 0,
  geometry: { type: "Point", coordinates: [34.544, 49.6025] },
  centroid: [34.544, 49.6025],
  region: { country_iso2: "UA" },
  constraints: [constraint],
  locale: "uk",
};

export const action: UIActionEnvelope = {
  id: "a1",
  field_context_revision: field.revision,
  trigger: "chip",
  label: "Шар вологості SMAP",
  action: { type: "map.set_layers", layer_ids: ["l_smap_rootzone"], mode: "replace" },
};

// The discriminated unions must narrow on their tag.
export function describe(event: AgentEvent): string {
  switch (event.event) {
    case "text.delta":
      return event.text;
    case "turn.completed":
      return `${event.message.blocks.length} blocks`;
    case "error":
      return event.code;
    default:
      return event.event;
  }
}

// @ts-expect-error unknown UI action types must not type-check
export const bad: UIActionEnvelope = { ...action, action: { type: "map.delete_field" } };
