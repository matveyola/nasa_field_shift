import { checkAgentEvent, isErrorEnvelope, requestProblem } from "../contracts";
import { validateAgentMessage } from "../contracts/validators.js";
import type { AgentEvent, AgentMessage, AgentRequest } from "../contracts";
import { type AbortReason, AgentError } from "./errors";
import { SSEParser } from "./sse";

export interface TransportOptions {
  /** Where the agent service is reachable from the browser, e.g. "/agent" behind a same-origin proxy. */
  baseUrl: string;
  /** Host-provided fetch, e.g. one that adds the app's session cookie for a backend-for-frontend. */
  fetch?: typeof fetch;
  /** Extra request headers from the host (a CSRF token, a short-lived user token). Never a server secret. */
  headers?: () => Record<string, string> | Promise<Record<string, string>>;
  /** Give up on a turn after this long. The service's own turn budget is 120 s by default. */
  deadlineMs?: number;
  /** Treat a stream as dead after this long without a byte. The service sends keep-alives every 15 s. */
  stallMs?: number;
}

export interface BackendStatus {
  state: "ready" | "not_ready" | "offline";
  agent: string | null; // "deterministic-dev", "llm:…" — shown so a demo never passes for a live model
  /** The real model the service runs (e.g. "gemini-3.8-flash"); never sent for the scripted demo model. */
  model?: string;
  knowledge: "unavailable" | "lexical" | "dense" | "hybrid" | "hybrid_rerank" | null; // null: agent has no knowledge tool
  /** Rotation simulation reachable by the agent, and how real it is. null: not connected. */
  simulation: { mode: "live" | "stub" | "fixture" } | null;
}

const DEFAULT_DEADLINE_MS = 150_000;
const DEFAULT_STALL_MS = 45_000;

function url(opts: TransportOptions, path: string): string {
  return opts.baseUrl.replace(/\/+$/, "") + path;
}

async function headers(opts: TransportOptions, request: AgentRequest, accept: string): Promise<Record<string, string>> {
  return {
    ...(opts.headers ? await opts.headers() : {}),
    "Content-Type": "application/json",
    Accept: accept,
    "X-Request-ID": request.request_id,
  };
}

/** Links the caller's signal, a deadline and a stall watchdog into one abort, and remembers why. */
class TurnAbort {
  readonly controller = new AbortController();
  cause: "caller" | "deadline" | "stall" | null = null;
  private deadline: ReturnType<typeof setTimeout>;
  private stall: ReturnType<typeof setTimeout> | null = null;
  private onCallerAbort = () => this.abort("caller");

  constructor(
    private caller: AbortSignal | undefined,
    deadlineMs: number,
    private stallMs: number,
  ) {
    this.deadline = setTimeout(() => this.abort("deadline"), deadlineMs);
    if (caller?.aborted) this.abort("caller");
    else caller?.addEventListener("abort", this.onCallerAbort, { once: true });
  }

  private abort(cause: "caller" | "deadline" | "stall") {
    if (this.cause) return;
    this.cause = cause;
    this.controller.abort();
  }

  /** Call whenever bytes arrive. */
  alive() {
    if (this.stall) clearTimeout(this.stall);
    this.stall = setTimeout(() => this.abort("stall"), this.stallMs);
  }

  /** The error to report if the turn ended because of an abort, else null. */
  error(): AgentError | null {
    if (!this.cause) return null;
    if (this.cause === "caller") {
      const reason = this.caller?.reason;
      const abortReason: AbortReason = reason === "field_changed" || reason === "unmounted" ? reason : "user";
      return new AgentError("aborted", false, { abortReason });
    }
    return new AgentError("timeout", true, { detail: this.cause });
  }

  dispose() {
    clearTimeout(this.deadline);
    if (this.stall) clearTimeout(this.stall);
    this.caller?.removeEventListener("abort", this.onCallerAbort);
  }
}

async function httpError(res: Response): Promise<AgentError> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // not JSON (a proxy's HTML error page): status only
  }
  if (isErrorEnvelope(body)) {
    const e = body.error;
    return new AgentError("http", e.retryable, { status: res.status, code: e.code, messageKey: e.message_key, requestId: e.request_id });
  }
  return new AgentError("http", res.status === 429 || res.status >= 500, { status: res.status });
}

function protocolError(detail: string): AgentError {
  return new AgentError("protocol", true, { detail });
}

export interface StreamHandlers {
  /** Progress events only (`turn.started`, `tool.*`, `text.delta`). The terminal outcome is the promise. */
  onEvent?(event: AgentEvent): void;
}

/**
 * Runs one turn over `POST /v1/agent/stream`.
 *
 * Exactly one outcome: resolves with the validated final `AgentMessage` (`turn.completed`) or rejects
 * with one `AgentError`. Every frame is checked against the M0 `AgentEvent` schema; anything else
 * (bad JSON, unknown event, event/type mismatch, a second `turn.started`) ends the turn as a
 * protocol error. Reading stops at the first terminal event, so a duplicated terminal is never seen.
 */
export async function streamTurn(
  opts: TransportOptions,
  request: AgentRequest,
  handlers: StreamHandlers = {},
  signal?: AbortSignal,
): Promise<AgentMessage> {
  const problem = requestProblem(request);
  if (problem) throw new AgentError("protocol", false, { code: "invalid_request", detail: `request does not match AgentRequest: ${problem}` });

  const abort = new TurnAbort(signal, opts.deadlineMs ?? DEFAULT_DEADLINE_MS, opts.stallMs ?? DEFAULT_STALL_MS);
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  try {
    let res: Response;
    try {
      res = await (opts.fetch ?? fetch)(url(opts, "/v1/agent/stream"), {
        method: "POST",
        headers: await headers(opts, request, "text/event-stream"),
        body: JSON.stringify(request),
        signal: abort.controller.signal,
        cache: "no-store",
      });
    } catch (e) {
      throw abort.error() ?? new AgentError("network", true, { detail: String(e) });
    }
    if (!res.ok) throw await httpError(res);
    if (!(res.headers.get("content-type") ?? "").startsWith("text/event-stream") || !res.body) {
      throw protocolError(`expected text/event-stream, got ${res.headers.get("content-type")}`);
    }

    reader = res.body.getReader();
    const parser = new SSEParser();
    let turnId: string | null = null;
    abort.alive();
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (e) {
        throw abort.error() ?? new AgentError("network", true, { detail: `stream interrupted: ${String(e)}` });
      }
      if (abort.cause) throw abort.error()!; // aborted between reads (some fetch polyfills keep delivering)
      abort.alive();
      const frames = chunk.done ? parser.end() : parser.push(chunk.value);
      for (const frame of frames) {
        let data: unknown;
        try {
          data = JSON.parse(frame.data);
        } catch {
          throw protocolError(`frame ${frame.id ?? "?"} is not JSON`);
        }
        const checked = checkAgentEvent(data);
        if (!checked.ok) throw protocolError(`frame ${frame.id ?? "?"} is not an AgentEvent: ${checked.reason}`);
        const event = checked.event;
        if (event.event !== frame.event) throw protocolError(`SSE event "${frame.event}" carries a "${event.event}" payload`);
        if (event.event === "turn.started") {
          if (turnId) throw protocolError("second turn.started");
          turnId = event.turn_id;
        } else if (!turnId) {
          throw protocolError(`${event.event} before turn.started`);
        }
        if (event.event === "turn.completed") {
          if (event.message.turn_id !== turnId) throw protocolError("turn.completed for another turn");
          return event.message;
        }
        if (event.event === "error") {
          throw new AgentError("server", event.retryable, { code: event.code, messageKey: event.message_key, requestId: event.request_id });
        }
        handlers.onEvent?.(event);
      }
      if (chunk.done) {
        throw new AgentError("network", true, { detail: "stream ended without turn.completed or error" });
      }
    }
  } finally {
    abort.dispose();
    // Stop reading (and tell the server) as soon as we have an outcome; the service cancels the turn.
    reader?.cancel().catch(() => undefined);
  }
}

/** One turn over `POST /v1/agent/message` (JSON). For environments without streaming and for tests. */
export async function sendMessage(opts: TransportOptions, request: AgentRequest, signal?: AbortSignal): Promise<AgentMessage> {
  const problem = requestProblem(request);
  if (problem) throw new AgentError("protocol", false, { code: "invalid_request", detail: `request does not match AgentRequest: ${problem}` });
  const abort = new TurnAbort(signal, opts.deadlineMs ?? DEFAULT_DEADLINE_MS, opts.deadlineMs ?? DEFAULT_DEADLINE_MS);
  try {
    let res: Response;
    let body: unknown;
    try {
      res = await (opts.fetch ?? fetch)(url(opts, "/v1/agent/message"), {
        method: "POST",
        headers: await headers(opts, request, "application/json"),
        body: JSON.stringify(request),
        signal: abort.controller.signal,
        cache: "no-store",
      });
      if (!res.ok) throw await httpError(res);
      body = await res.json();
    } catch (e) {
      if (e instanceof AgentError) throw e;
      throw abort.error() ?? new AgentError("network", true, { detail: String(e) });
    }
    if (!validateAgentMessage(body)) throw protocolError("response is not an AgentMessage");
    return body as AgentMessage;
  } finally {
    abort.dispose();
  }
}

/** `GET /health/ready`: whether the service is up and which agent and knowledge mode it runs. */
export async function checkBackend(opts: TransportOptions, signal?: AbortSignal): Promise<BackendStatus> {
  try {
    const res = await (opts.fetch ?? fetch)(url(opts, "/health/ready"), { signal, cache: "no-store" });
    const body = (await res.json().catch(() => null)) as Record<string, unknown> | null;
    const knowledge = body && typeof body.knowledge === "object" && body.knowledge ? (body.knowledge as Record<string, unknown>).mode : null;
    const modes = ["unavailable", "lexical", "dense", "hybrid", "hybrid_rerank"] as const;
    const integrations = body && typeof body.integrations === "object" && body.integrations ? (body.integrations as Record<string, unknown>) : null;
    const sim = integrations && typeof integrations.simulation === "object" ? (integrations.simulation as Record<string, unknown>) : null;
    const simMode = (["live", "stub", "fixture"] as const).find((m) => m === sim?.service_mode);
    return {
      state: res.ok && body?.status === "ready" ? "ready" : res.status === 503 ? "not_ready" : "offline",
      agent: typeof body?.agent === "string" ? body.agent : null,
      model: typeof body?.model === "string" && body.model ? body.model : undefined,
      knowledge: modes.find((m) => m === knowledge) ?? null,
      simulation: sim?.connected === true && sim.compare_rotations === true && simMode ? { mode: simMode } : null,
    };
  } catch {
    return { state: "offline", agent: null, knowledge: null, simulation: null };
  }
}
