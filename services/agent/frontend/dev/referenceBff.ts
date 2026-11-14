import { createHash, createHmac, randomUUID } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import type { ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import type { Plugin } from "vite";

/**
 * DEV-ONLY reference host backend (BFF): `/agent-bff/*` → the agent that requires signed requests.
 *
 * It demonstrates the signing half of the host boundary in a real browser flow. It is NOT authentication:
 * the principal is a fixed demo id, there is no login, no field-access decision and no rate limiting. A real
 * BFF must add all of those (docs/M6C_TEAM_HANDOFF.md). What it does do the way a real BFF must:
 *   - keeps the agent API key and the signing key on the server (never in the browser bundle);
 *   - replaces the browser's field data (facts, layers, charts, geometry) with the host's own copy, so a
 *     browser can never get its own "observations" signed; farmer-owned state (constraints, UI state,
 *     revision) is passed through, because in this demo the browser holds it;
 *   - signs the exact bytes it forwards: principal, field, revision, body SHA-256, 120 s expiry.
 * Configure with FS_BFF_SIGNING_KEY (= AGENT_CONTEXT_SIGNING_KEY of the agent) and FS_BFF_AGENT_KEY
 * (= AGENT_API_KEY). Without them the route answers 503.
 */

const b64 = (buf: Buffer | string) => Buffer.from(buf).toString("base64url");

export function attest(key: string, a: { principal: string; fieldId: string; revision: number; body: Buffer; lifetimeS?: number }): string {
  const iat = Math.floor(Date.now() / 1000);
  const payload = {
    v: 1, principal: a.principal, field_id: a.fieldId, field_context_revision: a.revision,
    body_sha256: createHash("sha256").update(a.body).digest("hex"), iat, exp: iat + (a.lifetimeS ?? 120),
    jti: randomUUID(), // each signature is unique; the agent accepts each token once
  };
  const encoded = b64(JSON.stringify(payload));
  return `v1.${encoded}.${b64(createHmac("sha256", key).update(`v1.${encoded}`).digest())}`;
}

// What the host owns and the browser may not change.
const HOST_OWNED = ["geometry", "centroid", "area_ha", "region", "facts", "timeseries_index", "available_layers", "available_charts"] as const;

function hostStore(): Map<string, Record<string, unknown>> {
  const dir = fileURLToPath(new URL("../../contracts/fixtures/", import.meta.url));
  const store = new Map<string, Record<string, unknown>>();
  for (const name of readdirSync(dir).filter((n) => n.startsWith("field_context."))) {
    const ctx = JSON.parse(readFileSync(dir + name, "utf8")) as Record<string, unknown>;
    store.set(ctx.field_id as string, ctx);
  }
  return store;
}

function refuse(res: ServerResponse, status: number, detail: string) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify({ error: { code: status === 403 ? "invalid_request" : "upstream_unavailable", retryable: false, detail,
    message_key: status === 403 ? "error.field_not_authorized" : "error.upstream_unavailable" } }));
}

export function referenceBff(): Plugin {
  const key = process.env.FS_BFF_SIGNING_KEY;
  const apiKey = process.env.FS_BFF_AGENT_KEY;
  const target = process.env.FS_AGENT_SIGNED_URL ?? "http://127.0.0.1:8002";
  const principal = process.env.FS_BFF_PRINCIPAL ?? "demo-farmer";
  const store = hostStore();
  return {
    name: "fieldshift-reference-bff",
    configureServer(server) {
      server.middlewares.use("/agent-bff", (req, res) => {
        void (async () => {
          if (!key || !apiKey) return refuse(res, 503, "reference BFF not configured (FS_BFF_SIGNING_KEY, FS_BFF_AGENT_KEY)");
          const headers: Record<string, string> = { authorization: `Bearer ${apiKey}`, accept: String(req.headers.accept ?? "*/*") };
          if (req.headers["x-request-id"]) headers["x-request-id"] = String(req.headers["x-request-id"]);
          let body: Buffer | undefined;
          if (req.method === "POST") {
            const chunks: Buffer[] = [];
            for await (const c of req) chunks.push(c as Buffer);
            const request = JSON.parse(Buffer.concat(chunks).toString("utf8")) as { field_context?: Record<string, unknown> };
            const own = store.get(String(request.field_context?.field_id));
            if (!request.field_context || !own) return refuse(res, 403, "field not in the host's store");
            for (const k of HOST_OWNED) {
              if (k in own) request.field_context[k] = own[k];
              else delete request.field_context[k];
            }
            body = Buffer.from(JSON.stringify(request));
            headers["content-type"] = "application/json";
            headers["x-fieldshift-context-attestation"] = attest(key, {
              principal, fieldId: String(request.field_context.field_id), revision: Number(request.field_context.revision), body,
            });
          }
          const abort = new AbortController();
          res.on("close", () => abort.abort()); // the farmer stopped or switched fields: stop the agent turn too
          const upstream = await fetch(target + (req.url ?? "/"), {
            method: req.method, headers, body: body && new Uint8Array(body), signal: abort.signal,
          });
          res.statusCode = upstream.status;
          res.setHeader("content-type", upstream.headers.get("content-type") ?? "application/json");
          if (upstream.body) for await (const chunk of upstream.body) res.write(chunk);
          res.end();
        })().catch(() => (res.headersSent ? res.end() : refuse(res, 503, "agent unreachable")));
      });
    },
  };
}
