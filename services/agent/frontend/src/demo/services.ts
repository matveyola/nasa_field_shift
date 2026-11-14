// The demo host's own calls to the geo and simulation services (through the dev proxy /integration →
// the contract stub). In production these go through the app's backend, which adds user authorization
// and the server-to-server credential; the browser never holds either.
import { validateFieldProfileResponse } from "../widget/contracts/validators.js";
import { checkComparisonResponse, type ComparisonResponse, type FieldContext, type KnownResult } from "../widget";

const BASE = "/integration";
const HEADERS = { Accept: "application/json", "X-FieldShift-Contract": "0.1" };

function requestId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** JSON with keys sorted, nulls and empty lists dropped: in M0 an absent list field equals [] (its default). */
function canonical(value: unknown): string {
  return JSON.stringify(value, (_k, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([, x]) => x !== null && !(Array.isArray(x) && x.length === 0)).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  );
}

export type ProfileStatus =
  | { state: "loaded"; mode: string; facts: number }
  | { state: "unavailable"; reason: string };

/** Fetch the field profile and merge it into the host's FieldContext. The host stays the owner of the context. */
export async function loadProfile(ctx: FieldContext): Promise<{ ctx: FieldContext; status: ProfileStatus }> {
  try {
    const rid = requestId("hostgeo");
    const res = await fetch(`${BASE}/geo/v1/field-profile`, {
      method: "POST",
      headers: { ...HEADERS, "Content-Type": "application/json", "X-Request-ID": rid },
      body: JSON.stringify({ contract_version: "0.1", request_id: rid, field_id: ctx.field_id }),
    });
    if (!res.ok) return { ctx, status: { state: "unavailable", reason: `HTTP ${res.status}` } };
    const body: unknown = await res.json();
    if (!validateFieldProfileResponse(body)) return { ctx, status: { state: "unavailable", reason: "contract mismatch" } };
    const resp = body as { request_id: string; service: { mode: string }; profile: Pick<FieldContext, "field_id" | "facts" | "timeseries_index" | "available_layers"> };
    if (resp.request_id !== rid || resp.profile.field_id !== ctx.field_id) {
      return { ctx, status: { state: "unavailable", reason: "profile for another request or field" } };
    }
    const merged: FieldContext = {
      ...ctx,
      facts: resp.profile.facts ?? [],
      timeseries_index: resp.profile.timeseries_index ?? [],
      available_layers: resp.profile.available_layers ?? [],
    };
    // revision changes only when the context really changes (here: the stub serves the same fixture data,
    // serialised with a different key order, so compare canonical forms)
    const changed = canonical([merged.facts, merged.timeseries_index, merged.available_layers]) !==
      canonical([ctx.facts ?? [], ctx.timeseries_index ?? [], ctx.available_layers ?? []]);
    return {
      ctx: changed ? { ...merged, revision: ctx.revision + 1 } : ctx,
      status: { state: "loaded", mode: resp.service.mode, facts: merged.facts?.length ?? 0 },
    };
  } catch {
    return { ctx, status: { state: "unavailable", reason: "geo service unreachable" } };
  }
}

/** The simulation service's mode ("stub", "live", ...) when it offers comparisons, else null. */
export async function simulationConnected(): Promise<string | null> {
  try {
    const res = await fetch(`${BASE}/sim/v1/capabilities`, { headers: HEADERS });
    const body = (await res.json()) as { service?: { mode?: string }; simulation?: { compare_rotations?: boolean } };
    return res.ok && body.simulation?.compare_rotations === true ? (body.service?.mode ?? "unknown") : null;
  } catch {
    return null;
  }
}

export interface FetchedResult extends KnownResult {
  response: ComparisonResponse;
}

/** GET a comparison by id from the simulation service and check it before the host shows anything. */
export async function fetchResult(resultId: string, fieldId: string): Promise<FetchedResult | null> {
  try {
    const res = await fetch(`${BASE}/sim/v1/comparisons/${encodeURIComponent(resultId)}`, {
      headers: { ...HEADERS, "X-Request-ID": requestId("hostsim") },
    });
    if (!res.ok) return null; // 404 unknown, 410 expired, 5xx down: the host shows nothing
    const checked = checkComparisonResponse(await res.json());
    if (!checked.ok) return null;
    const r = checked.value;
    if (r.result_id !== resultId || r.field_id !== fieldId || r.status !== "completed" || !r.comparison) return null;
    return {
      result_id: r.result_id,
      field_id: r.field_id,
      field_context_revision: r.field_context_revision,
      expires_at: r.expires_at,
      rotation_ids: r.comparison.rotations.map((x) => x.rotation_id),
      scenario: r.scenario,
      response: r,
    };
  } catch {
    return null;
  }
}
