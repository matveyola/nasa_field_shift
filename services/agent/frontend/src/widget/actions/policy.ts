import { isUIActionEnvelope } from "../contracts";
import type { AppScreen, Constraint, FieldContext, UIAction, UIActionEnvelope, UIState } from "../contracts";

export type ActionType = UIAction["type"];

/** What the host application can do right now. The widget never offers more than this. */
export interface HostCapabilities {
  /** Action types the host can apply (its map, chart, comparison and panel modules). */
  actions: readonly ActionType[];
  /** A rotation-comparison / simulation engine is connected. */
  simulation: boolean;
  /** The app's own screens the assistant may open (`app.open_screen`) or point to with `how_to_open`. */
  screens?: readonly AppScreen[];
}

export type RejectReason =
  | "field_changed" // made for another field
  | "stale_context" // the field changed since the agent saw it
  | "module_unavailable" // the host has no module for this action
  | "unknown_reference" // a layer / chart / metric id the host does not have
  | "not_permitted" // e.g. constraints the farmer has not confirmed
  | "expired" // references a simulation result past its expires_at
  | "invalid" // not a contract-valid envelope
  | "failed" // the host tried and failed
  | "cancelled"; // the farmer chose not to go ahead (e.g. kept an earlier decision this would replace)

export interface ActionRequest {
  /** Revision already re-checked by the widget; the host checks it again against its live store. */
  envelope: UIActionEnvelope;
  /** Field the action was made for. */
  fieldId: string;
  /** `agent_auto`: an `auto` display action. `farmer_click`: the farmer pressed the chip or confirmed. */
  origin: "agent_auto" | "farmer_click";
}

export type ActionResult =
  | { status: "applied"; /** The FieldContext revision after applying, when it changed. */ revision?: number }
  | { status: "rejected"; reason: RejectReason };

/** Changes the farmer's comparison or preferences: never applied without a click, whatever `trigger` says. */
const ALWAYS_CONFIRM: ReadonlySet<ActionType> = new Set(["compare.set_constraints", "compare.select_rotations", "scenario.set"]);

export function requiresClick(envelope: UIActionEnvelope): boolean {
  return envelope.trigger === "chip" || ALWAYS_CONFIRM.has(envelope.action.type);
}

function constraintsProblem(constraints: Constraint[]): RejectReason | null {
  // Only farmer-confirmed (or explicitly rejected) constraints may reach the comparison.
  return constraints.every((c) => c.status === "confirmed" || c.status === "rejected") ? null : "not_permitted";
}

/**
 * The host's last line of defence (CONTRACTS.md › UIActionEnvelope). Mirrors the agent service's
 * `ui_action_problem`, plus field identity, host capability and confirmation rules.
 * Returns null when the action may be applied to `ctx`.
 */
export function actionProblem(
  envelope: unknown,
  ctx: FieldContext | null,
  caps: HostCapabilities,
  fieldId: string,
  results: ReadonlyMap<string, KnownResult> = new Map(),
  now: Date = new Date(),
): RejectReason | null {
  if (!isUIActionEnvelope(envelope)) return "invalid";
  if (!ctx || ctx.field_id !== fieldId) return "field_changed";
  if (envelope.field_context_revision !== ctx.revision) return "stale_context";
  const action = envelope.action;
  if (!caps.actions.includes(action.type)) return "module_unavailable";
  switch (action.type) {
    case "map.set_layers": {
      const known = new Set((ctx.available_layers ?? []).map((l) => l.layer_id));
      return action.layer_ids.every((id) => known.has(id)) ? null : "unknown_reference";
    }
    case "chart.focus": {
      const chart = (ctx.available_charts ?? []).find((c) => c.chart_id === action.chart_id);
      return chart && action.metric_ids.every((m) => chart.metric_ids.includes(m)) ? null : "unknown_reference";
    }
    case "time.set_range":
      return action.start <= action.end ? null : "invalid";
    case "compare.set_constraints":
      return constraintsProblem(action.constraints);
    case "compare.select_rotations": {
      // Rotation ids exist only in a simulation result the host has fetched and checked itself.
      if (!caps.simulation) return "module_unavailable";
      const live = [...results.values()].filter((r) => resultIsLive(r, ctx, now));
      if (!live.length) return "unknown_reference";
      return action.rotation_ids.every((id) => live.some((r) => r.rotation_ids.includes(id))) ? null : "unknown_reference";
    }
    case "panel.open": {
      if (action.panel !== "comparison" || !action.ref_id) return null;
      const result = results.get(action.ref_id);
      if (!result) return "unknown_reference";
      if (result.field_id !== ctx.field_id) return "field_changed";
      // A result for another scenario never answers this one (M6C): the agent server names the scenario.
      if (action.scenario != null && result.scenario !== action.scenario) return "unknown_reference";
      // A result computed for a context this host never had (a later revision) is a replay or a foreign context.
      if (result.field_context_revision != null && result.field_context_revision > ctx.revision) return "stale_context";
      return resultIsLive(result, ctx, now) ? null : "expired";
    }
    case "app.open_screen":
      return (caps.screens ?? []).some((s) => s.screen_id === action.screen_id) ? null : "unknown_reference";
    case "scenario.set":
    case "map.focus_field":
      return null;
  }
}

/**
 * The action that puts back what an automatic view change replaced, from the view as it was before the answer.
 * Only view changes have one; null when there is nothing to go back to (e.g. no screen or period was set).
 */
export function backAction(type: ActionType, before: UIState): UIAction | null {
  switch (type) {
    case "map.set_layers":
      return before.active_layer_ids?.length ? { type, layer_ids: [...before.active_layer_ids], mode: "replace" } : null;
    case "time.set_range":
      return before.time_range ? { type, ...before.time_range } : null;
    case "chart.focus":
      return before.chart_focus ? { type, ...before.chart_focus } : null;
    case "app.open_screen":
      return before.screen_id ? { type, screen_id: before.screen_id } : null;
    default:
      return null;
  }
}

/** A simulation result as the host knows it (from GET /v1/comparisons/{id} of the simulation service). */
export interface KnownResult {
  result_id: string;
  field_id: string;
  /** Revision the simulation was computed for (ComparisonResponse.field_context_revision). */
  field_context_revision?: number;
  expires_at: string; // RFC 3339
  rotation_ids: readonly string[];
  /** Scenario the result was computed for (ComparisonResponse.scenario). */
  scenario?: string;
}

function resultIsLive(r: KnownResult, ctx: FieldContext, now: Date): boolean {
  return (
    r.field_id === ctx.field_id &&
    (r.field_context_revision == null || r.field_context_revision <= ctx.revision) &&
    Date.parse(r.expires_at) > now.getTime()
  );
}

/**
 * Revisions under which a message's actions are still valid: the one the agent answered for, plus
 * each revision produced by applying that same message's own actions. Applying "show SMAP" bumps
 * the revision, and the message's other chips must stay usable; any other change makes them stale.
 */
export function isCurrent(ctx: FieldContext | null, fieldId: string, validRevisions: readonly number[]): boolean {
  return !!ctx && ctx.field_id === fieldId && validRevisions.includes(ctx.revision);
}

/** What a farmer decides about a proposed constraint: keep it as a planning condition, or reject it. */
export type ConstraintDecision = "confirmed" | "rejected";

/**
 * A farmer's decision about one proposed constraint, as the typed action the host applies. Only the farmer's own
 * click builds one (a `chip`); the agent's actions never decide for the farmer.
 */
export function confirmationEnvelope(
  constraint: Constraint,
  ctx: FieldContext,
  label: string,
  decision: ConstraintDecision = "confirmed",
): UIActionEnvelope {
  return {
    id: `${decision === "confirmed" ? "confirm" : "reject"}.${constraint.id}`,
    field_context_revision: ctx.revision,
    trigger: "chip",
    label: label.slice(0, 300),
    action: { type: "compare.set_constraints", constraints: [{ ...constraint, status: decision }] },
  };
}
