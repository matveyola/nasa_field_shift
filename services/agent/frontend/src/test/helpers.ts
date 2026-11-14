// Test doubles for the agent service: deterministic SSE responses built from M0 fixtures.
import type { AgentEvent, AgentMessage, AgentRequest, FieldContext } from "../widget/contracts";
import events from "../../../contracts/fixtures/agent_events.poltava.json";
import kansasCtx from "../../../contracts/fixtures/field_context.kansas.json";
import poltavaCtx from "../../../contracts/fixtures/field_context.poltava.json";
import request from "../../../contracts/fixtures/agent_request.kansas.json";

export const fixtureEvents = events as unknown as AgentEvent[];
export const fixtureMessage = (fixtureEvents.at(-1) as { message: AgentMessage }).message;
export const poltava = () => structuredClone(poltavaCtx) as unknown as FieldContext;
export const kansas = () => structuredClone(kansasCtx) as unknown as FieldContext;
export const fixtureRequest = () => structuredClone(request) as unknown as AgentRequest;

const enc = new TextEncoder();

export function frame(e: AgentEvent | Record<string, unknown>, id?: number, name?: string): string {
  const ev = name ?? (e as { event: string }).event;
  return `event: ${ev}\n${id != null ? `id: ${id}\n` : ""}data: ${JSON.stringify(e)}\n\n`;
}

export function sse(list: (AgentEvent | Record<string, unknown>)[]): string {
  return list.map((e, i) => frame(e, i + 1)).join("");
}

/** A turn: started → (progress) → completed with `message`, using the message's turn id. */
export function turn(message: AgentMessage, progress: AgentEvent[] = []): AgentEvent[] {
  return [{ event: "turn.started", turn_id: message.turn_id }, ...progress, { event: "turn.completed", message }];
}

export interface FakeServer {
  fetch: typeof fetch;
  requests: { url: string; body: unknown; headers: Record<string, string> }[];
}

type Reply = string | Uint8Array | (string | Uint8Array)[] | Response | Error | ((init: RequestInit) => Response | Promise<Response>);

/** fetch() double: answers each call with the next reply (SSE text, chunks, a Response, or an error). */
export function fakeServer(...replies: Reply[]): FakeServer {
  const requests: FakeServer["requests"] = [];
  let n = 0;
  const f = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url.endsWith("/health/ready")) {
      return new Response(JSON.stringify({ status: "ready", agent: "deterministic-dev" }), { status: 200, headers: { "content-type": "application/json" } });
    }
    requests.push({ url, body: init.body ? JSON.parse(String(init.body)) : null, headers: Object.fromEntries(new Headers(init.headers).entries()) });
    const reply = replies[Math.min(n++, replies.length - 1)]!;
    if (reply instanceof Error) throw reply;
    if (reply instanceof Response) return reply;
    if (typeof reply === "function") return reply(init);
    const chunks = Array.isArray(reply) ? reply : [reply];
    return streamResponse(chunks, init.signal ?? undefined);
  };
  return { fetch: f as typeof fetch, requests };
}

/** An SSE response whose body yields `chunks`, then ends. Aborting the signal errors the body, like a browser. */
export function streamResponse(chunks: (string | Uint8Array)[], signal?: AbortSignal, opts: { hold?: boolean } = {}): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      signal?.addEventListener("abort", () => controller.error(new DOMException("The operation was aborted.", "AbortError")), { once: true });
    },
    async pull(controller) {
      await Promise.resolve();
      if (i < chunks.length) {
        const c = chunks[i++]!;
        controller.enqueue(typeof c === "string" ? enc.encode(c) : c);
      } else if (!opts.hold) {
        controller.close();
      }
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

/** A stream the test drives by hand: push frames, then end, error, or leave it hanging. */
export function manualStream() {
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  let signal: AbortSignal | undefined;
  const fetchFn = (async (_input: RequestInfo | URL, init: RequestInit = {}) => {
    if (String(_input).endsWith("/health/ready")) {
      return new Response(JSON.stringify({ status: "ready", agent: "deterministic-dev" }), { status: 200 });
    }
    signal = init.signal ?? undefined;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        ctrl = c;
        signal?.addEventListener("abort", () => c.error(new DOMException("The operation was aborted.", "AbortError")), { once: true });
      },
    });
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  }) as typeof fetch;
  return {
    fetch: fetchFn,
    push: (text: string) => ctrl.enqueue(enc.encode(text)),
    end: () => ctrl.close(),
    get aborted() {
      return signal?.aborted ?? false;
    },
  };
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** matchMedia stub: `matches` for the given queries. */
export function mockMedia(matching: string[]) {
  window.matchMedia = ((query: string) =>
    ({
      matches: matching.includes(query),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList) as typeof window.matchMedia;
}
