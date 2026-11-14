// The demo host's action adapter: validate with the shared policy, then apply to the host's own store.
// A real host does the same against its map (MapLibre / Deck.gl), chart and comparison modules.
import {
  actionProblem,
  type ActionRequest,
  type ActionResult,
  type Constraint,
  type FieldContext,
  type HostCapabilities,
  type KnownResult,
} from "../widget";

/** No chart module in this demo. The comparison view exists only while the simulation service answers. */
export function demoCapabilities(simulation: boolean): HostCapabilities {
  return {
    actions: [
      "map.set_layers",
      "map.focus_field",
      "compare.set_constraints",
      "scenario.set",
      "panel.open",
      "app.open_screen",
      ...(simulation ? (["compare.select_rotations"] as const) : []),
    ],
    simulation,
    screens: DEMO_SCREENS,
  };
}

/** Pretend app screens, only to test opening a screen and "where to find it" (the real app has its own). */
export const DEMO_SCREENS = [
  { screen_id: "field_map", label: "Карта поля", how_to_open: "Головна → Карта" },
  { screen_id: "soil", label: "Ґрунт поля", how_to_open: "Поле → вкладка «Ґрунт»" },
  { screen_id: "weather", label: "Погода", how_to_open: "Меню → Погода" },
  { screen_id: "rotations", label: "Сівозміни", how_to_open: "Поле → вкладка «Сівозміни»" },
];

export interface LogEntry {
  /** The field the action was for: the log shows only the selected field's actions. */
  fieldId: string;
  at: string;
  type: string;
  label: string;
  origin: ActionRequest["origin"];
  outcome: "applied" | "rejected";
  detail: string;
}

export interface HostOutcome {
  result: ActionResult;
  next: FieldContext | null;
  /** The comparison result to show, when the action opened the comparison panel. */
  openComparison?: string;
}

/** Applies a validated action; returns the new context (revision + 1 when it changed) or the rejection reason. */
export function applyHostAction(
  ctx: FieldContext | null,
  req: ActionRequest,
  caps: HostCapabilities,
  results: ReadonlyMap<string, KnownResult>,
): HostOutcome {
  const problem = actionProblem(req.envelope, ctx, caps, req.fieldId, results);
  if (problem || !ctx) return { result: { status: "rejected", reason: problem ?? "field_changed" }, next: null };
  const a = req.envelope.action;
  const ui = { ...ctx.ui_state };
  let constraints = ctx.constraints ?? [];
  switch (a.type) {
    case "map.set_layers":
      ui.active_layer_ids = a.mode === "replace" ? [...a.layer_ids] : [...new Set([...(ui.active_layer_ids ?? []), ...a.layer_ids])];
      break;
    case "scenario.set":
      ui.scenario = a.scenario;
      break;
    case "compare.set_constraints":
      // One constraint per type: a confirmed value replaces the earlier one.
      constraints = [...constraints.filter((c) => !a.constraints.some((n: Constraint) => n.type === c.type)), ...a.constraints];
      break;
    case "compare.select_rotations":
      ui.selected_rotation_ids = [...a.rotation_ids];
      break;
    case "app.open_screen":
      ui.screen_id = a.screen_id;
      break;
    case "map.focus_field":
      return { result: { status: "applied" }, next: ctx }; // the map re-centres; the context does not change
    case "panel.open":
      // Opening a panel is view state outside FieldContext (M0 UIState has no panel field): no revision change.
      return { result: { status: "applied" }, next: ctx, openComparison: a.panel === "comparison" ? (a.ref_id ?? undefined) : undefined };
    default:
      return { result: { status: "rejected", reason: "module_unavailable" }, next: null };
  }
  const next: FieldContext = { ...ctx, ui_state: ui, constraints, revision: ctx.revision + 1 };
  return { result: { status: "applied", revision: next.revision }, next };
}
