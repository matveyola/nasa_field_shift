import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Page, Route } from "@playwright/test";

// Replays SSE recorded from the real agent service (record_fixtures.py) keyed by the farmer's text, and
// stands in for the geo / simulation services with contract-shaped answers built from the same M0 fixtures.
const dir = fileURLToPath(new URL("./fixtures/", import.meta.url));
const contracts = fileURLToPath(new URL("../../contracts/fixtures/", import.meta.url));
const index = JSON.parse(readFileSync(dir + "index.json", "utf8")) as Record<string, string>;
const byText = new Map(Object.entries(index).map(([name, text]) => [text, readFileSync(`${dir}${name}.sse`, "utf8")]));
const comparison = JSON.parse(readFileSync(dir + "compare_uk.result.json", "utf8")) as Record<string, unknown>;
const contexts = ["poltava", "kansas", "mykolaiv"].map((f) => JSON.parse(readFileSync(`${contracts}field_context.${f}.json`, "utf8")));

export const TEXTS = index as Record<"field_uk" | "constraints_uk" | "map_uk" | "simulation_uk" | "fao_uk" | "compare_uk", string>;
export const RESULT_ID = comparison.result_id as string;

const STUB = { implementation: "fieldshift-contract-stub 0.1", mode: "stub", contract_version: "0.1" };

export interface MockOptions {
  /** Hold the stream open (never answer) for these texts: used to switch fields mid-answer. */
  hang?: string[];
  /** Refuse connections, as when the service is down. */
  offline?: boolean;
  /** The simulation service answers 503 (not connected). */
  simulationDown?: boolean;
  /** The simulation service no longer knows the result (404), as for an invented or purged id. */
  resultGone?: boolean;
}

export async function mockAgent(page: Page, opts: MockOptions = {}) {
  const requests: { text: string; body: Record<string, unknown> }[] = [];
  const simulation = opts.simulationDown
    ? { configured: "http", connected: false, problem: "unavailable" }
    : { configured: "http", connected: true, service_mode: "stub", compare_rotations: true, scenarios: ["typical"] };
  const health = (agent: string, knowledge?: string) => (route: Route) =>
    opts.offline
      ? route.abort("connectionrefused")
      : route.fulfill({
          json: { status: "ready", agent, ...(knowledge ? { knowledge: { mode: knowledge }, integrations: { simulation } } : {}) },
        });
  await page.route("**/agent/health/ready", health("deterministic-dev"));
  await page.route("**/agent-llm/health/ready", health("llm/scripted-llm", "lexical"));
  await page.route(/\/agent(-llm)?\/v1\/agent\/stream$/, async (route) => {
    if (opts.offline) return route.abort("connectionrefused");
    const body = route.request().postDataJSON() as { message: { text: string } };
    requests.push({ text: body.message.text, body });
    if (opts.hang?.includes(body.message.text)) return; // never fulfilled: the client must give up or abort
    const sse = byText.get(body.message.text);
    if (!sse) return route.fulfill({ status: 500, body: "no fixture for this text" });
    return route.fulfill({ status: 200, headers: { "content-type": "text/event-stream; charset=utf-8" }, body: sse });
  });

  // ---- the host's own calls to the services (proposed integration contract v0.1)
  await page.route("**/integration/geo/v1/field-profile", (route) => {
    const req = route.request().postDataJSON() as { request_id: string; field_id: string };
    const ctx = contexts.find((c) => c.field_id === req.field_id);
    if (!ctx) return route.fulfill({ status: 404, json: { error: { code: "not_found", message_key: "error.not_found", retryable: false } } });
    return route.fulfill({
      json: {
        contract_version: "0.1", request_id: req.request_id, service: { service: "geo", ...STUB }, status: "complete",
        generated_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"), geometry: ctx.geometry, centroid: ctx.centroid,
        profile: { field_id: ctx.field_id, facts: ctx.facts ?? [], timeseries_index: ctx.timeseries_index ?? [], available_layers: ctx.available_layers ?? [] },
      },
    });
  });
  await page.route("**/integration/sim/v1/capabilities", (route) =>
    opts.simulationDown
      ? route.fulfill({ status: 503, json: { error: { code: "unavailable", message_key: "error.unavailable", retryable: true } } })
      : route.fulfill({
          json: { service: { service: "simulation", ...STUB }, simulation: { compare_rotations: true, scenarios: ["typical"], max_rotations: 6, result_ttl_s: 3600, async_jobs: false } },
        }),
  );
  await page.route("**/integration/sim/v1/comparisons/*", (route) => {
    const id = decodeURIComponent(route.request().url().split("/").pop()!);
    if (opts.resultGone || id !== RESULT_ID) {
      return route.fulfill({ status: 404, json: { error: { code: "not_found", message_key: "error.not_found", retryable: false } } });
    }
    // The recorded result is replayed with fresh validity, as the service would serve it within its TTL.
    const now = Date.now();
    const iso = (t: number) => new Date(t).toISOString().replace(/\.\d+Z$/, "Z");
    return route.fulfill({ json: { ...comparison, created_at: iso(now - 60_000), computed_at: iso(now - 60_000), expires_at: iso(now + 3_600_000) } });
  });
  return requests;
}
