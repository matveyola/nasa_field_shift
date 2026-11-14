# M6B — Team onboarding: connecting a real host, geo or simulation service

For members 1 (geo), 2 (simulation), 4 and 5 (host app and its backend). Contract: integration v0.1
(**proposed**, `docs/M5_TEAM_INTEGRATION_CONTRACT.md`). Nothing below has been verified against a real
teammate service yet: none was available when this was written.

## 1. The chain of trust

```text
Browser (widget)  ──cookie/session──▶  Host backend (BFF)  ──API key + signed attestation──▶  Agent service
                                         │ authenticates the farmer                          │ verifies the signature,
                                         │ authorizes the field (OWASP API1: BOLA)           │ body hash, field, revision,
                                         │ builds FieldContext from its store + geo          │ expiry; isolates by principal
                                         ▼                                                   ▼
                                   geo / simulation  ◀──────── service bearer token ──────────┘
```

- **A reference id is not permission.** `field_id`, `result_id` and `session_id` identify things; they never
  authorize. The BFF decides whether *this farmer* may see *this field*; services must refuse fields the
  caller may not access (`403 field_not_authorized`); the host fetches results by id only through its backend
  and checks `field_id`, `status`, `expires_at` and revision before showing them.
- **The agent has no user accounts.** Its only evidence about who is asking is a valid attestation. Headers
  such as `X-Forwarded-User` or `X-FieldShift-Principal`, body fields and the API key alone are never identity.
- **Hooks the host must provide** (the agent cannot verify them): farmer authentication; field access check;
  building `FieldContext.facts` from the geo service; signing; rate limiting; result access through its backend.
  Until those exist, the boundary is *implemented on the agent side only*.

## 2. Host backend: sign every request (members 4, 5)

Header `X-FieldShift-Context-Attestation: v1.<base64url(payload JSON)>.<base64url(HMAC-SHA256)>`, MAC over
the ASCII string `v1.<encoded payload>`, key `AGENT_CONTEXT_SIGNING_KEY` (≥ 32 chars, shared only by the BFF
and the agent, never the browser, different from `AGENT_API_KEY`).

| Payload field | Meaning |
|---|---|
| `v` | `1` |
| `principal` | opaque id of the authenticated farmer, `[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}` (no e-mail needed) |
| `field_id`, `field_context_revision` | the field and revision you authorized; must equal the body's |
| `body_sha256` | hex SHA-256 of the **exact bytes** you send as the body (sign after serializing) |
| `iat`, `exp` | unix seconds; `exp - iat ≤ 300`; 30 s clock skew allowed |

Node.js (tested against the agent's verifier):

```js
import { createHash, createHmac } from "node:crypto";
const b64 = (buf) => Buffer.from(buf).toString("base64url");

export function attest(key, { principal, fieldId, revision, body, lifetimeS = 120 }) {
  const iat = Math.floor(Date.now() / 1000);
  const payload = { v: 1, principal, field_id: fieldId, field_context_revision: revision,
    body_sha256: createHash("sha256").update(body).digest("hex"), iat, exp: iat + lifetimeS };
  const encoded = b64(JSON.stringify(payload));
  const mac = createHmac("sha256", key).update(`v1.${encoded}`).digest();
  return `v1.${encoded}.${b64(mac)}`;
}
// const body = Buffer.from(JSON.stringify(agentRequest));
// fetch(`${AGENT}/v1/agent/stream`, { method: "POST", body, headers: { "Content-Type": "application/json",
//   Authorization: `Bearer ${AGENT_API_KEY}`, "X-FieldShift-Context-Attestation": attest(KEY, {...}) } });
```

Python reference: `agent_service.attestation.sign`. Refusals are `403` with a safe reason
(`malformed`, `bad_signature`, `bad_lifetime`, `expired`, `body_mismatch`, `field_mismatch`,
`revision_mismatch`, `bad_principal`); the token is never echoed or logged.

What a valid attestation means: the BFF vouches for this body, field, revision and caller. Field facts
become `host_attested` (shown without the "not confirmed" label). It does **not** make a value scientifically
authentic: provenance still says where it came from, and a configured geo service still wins on conflict.

Known limit: there is no nonce store, so an attestation can be re-sent unchanged until it expires (≤ 300 s).
That replays the identical request only (any change breaks the body hash) and simulations are de-duplicated.
Add a `jti` store if the threat model needs it.

## 3. Services: what the agent sends and checks (members 1, 2)

Every call: `Authorization: Bearer <AGENT_INTEGRATION_TOKEN>`, `X-Request-ID`, `X-FieldShift-Contract: 0.1`,
JSON only, no redirects, ≤ 2 MB. Comparisons also carry `Idempotency-Key` (scoped to the verified principal).
Sample bodies: `uv run python -m agent_service.integrations.contract_check --examples`.

Four separate notions of "who answered", never mixed (shown in `/health/ready → integrations.*.transport`):
1. **configured endpoint** (our config); 2. **transport-authenticated identity** (TLS certificate against the
system trust store; plain `http` authenticates nothing and is refused in production); 3. **claimed identity**
(`service.implementation`/`mode` in your capabilities: reported, never trusted as authentication);
4. **per-value provenance** (`kind`, `data_status`, source, version: scientific origin, checked for consistency,
never proof of authenticity). Only `mode: "live"` counts as real; stubs and fixtures label every value `fixture`.

## 4. Configuration

| Agent variable | For |
|---|---|
| `AGENT_API_KEY` | BFF → agent bearer (≥ 32 chars in production) |
| `AGENT_CONTEXT_SIGNING_KEY`, `AGENT_REQUIRE_CONTEXT_ATTESTATION` | attestation key; `true` refuses unsigned requests (mandatory in production) |
| `AGENT_GEO_PROVIDER=http`, `AGENT_GEO_URL` | geo base URL ending at the prefix (`https://…/geo`) |
| `AGENT_SIM_PROVIDER=http`, `AGENT_SIM_URL` | simulation base URL (`https://…/sim`) |
| `AGENT_INTEGRATION_TOKEN` | agent → services bearer |
| `AGENT_INTEGRATION_TIMEOUT_S`, `AGENT_SIM_TIMEOUT_S`, `AGENT_INTEGRATION_MAX_BYTES` | 8 s, 30 s, 2 MB |

All secrets live in `.env` (git-ignored) or your secret store; none may reach the frontend bundle.
`AGENT_CONTEXT_ATTESTED` was removed; setting it stops startup with an explanation.

## 5. Check your service

```bash
uv run python -m agent_service.integrations.contract_check --geo https://YOUR-GEO/geo --token-env GEO_TOKEN --partial-field FIELD_WITH_GAPS --json geo-report.json
```

```bash
uv run python -m agent_service.integrations.contract_check --sim https://YOUR-SIM/sim --field YOUR-FIELD --context field_context.json --token-env SIM_TOKEN --json sim-report.json
```

| Outcome | Meaning |
|---|---|
| `PASS` | checked and conforms (the agent can consume it; says nothing about scientific quality) |
| `FAIL` | checked and does not conform: field path, expectation, category and a fix are printed |
| `SKIPPED` | not applicable or not forceable here (reason printed), e.g. no `--partial-field` |
| `BLOCKED_NO_CREDENTIALS` | needs a credential not given, e.g. the authentication check without `--token-env` |
| `BLOCKED_NO_SERVICE` | the service did not answer; dependent checks did not run |
| `NOT_PERFORMED` | no URL given for that service |

Only PASS counts as passed. The last line is the **real-integration verdict**: `VERIFIED` only for a
`mode: "live"` service with no FAIL or BLOCKED outcome; a stub or fixture is always `NOT VERIFIED`.
Exit code 1 on any FAIL or BLOCKED_NO_SERVICE. Tokens and response bodies are never printed.

Practise against the stub first: `uv run python -m integration_stub --port 8100` (set `STUB_TOKEN` to make
it require a bearer), then use `http://127.0.0.1:8100/geo` and `/sim`.

## 6. After your service passes

1. Point the agent at it (§4), restart, and check `/health/ready`: `service_mode: "live"`, `transport` as expected.
2. Run the canonical scenario through the real host: field Poltava, text «У мене немає поливу. Покажи вологість
   ґрунту і порівняй дві сівозміни під час посухи.» Compare with `evals/reports/m6b_acceptance.md` (stub run).
3. Report failures with: the checker `--json` report, your `X-Request-ID` values, the agent's
   `/health/ready`, and agent log lines starting `integration service=` (ids, statuses and timings only).
