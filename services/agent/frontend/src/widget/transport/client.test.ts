import { afterEach, describe, expect, it, vi } from "vitest";
import { fakeServer, fixtureEvents, fixtureMessage, fixtureRequest, frame, jsonResponse, manualStream, sse, turn } from "../../test/helpers";
import type { AgentEvent } from "../contracts";
import { requestProblem } from "../contracts";
import { checkBackend, sendMessage, streamTurn } from "./client";
import { AgentError, errorTopic } from "./errors";

const opts = (f: typeof fetch, extra = {}) => ({ baseUrl: "/agent", fetch: f, ...extra });

async function failure(p: Promise<unknown>): Promise<AgentError> {
  try {
    await p;
  } catch (e) {
    if (e instanceof AgentError) return e;
    throw e;
  }
  throw new Error("expected the turn to fail");
}

afterEach(() => vi.useRealTimers());

describe("streamTurn", () => {
  it("delivers progress events and resolves with the validated final message", async () => {
    const server = fakeServer(sse(fixtureEvents));
    const seen: string[] = [];
    const message = await streamTurn(opts(server.fetch), fixtureRequest(), { onEvent: (e) => seen.push(e.event) });
    expect(message).toEqual(fixtureMessage);
    expect(seen).toEqual(["turn.started", "tool.started", "tool.finished", "tool.started", "tool.finished", "text.delta", "text.delta"]);
    expect(server.requests[0]!.url).toBe("/agent/v1/agent/stream");
    expect(server.requests[0]!.headers["x-request-id"]).toBe(fixtureRequest().request_id);
    expect(requestProblem(server.requests[0]!.body)).toBeNull();
  });

  it("survives arbitrary chunking: one byte at a time, keep-alives in between", async () => {
    const text = sse(fixtureEvents).replace(/\n\n/g, "\n\n: ping\n\n");
    const bytes = new TextEncoder().encode(text);
    const server = fakeServer([...bytes].map((b) => Uint8Array.of(b)));
    await expect(streamTurn(opts(server.fetch), fixtureRequest())).resolves.toEqual(fixtureMessage);
  });

  it.each([
    ["invalid JSON", 'event: turn.started\ndata: {not json\n\n'],
    ["an unknown event type", frame({ event: "turn.paused", turn_id: "t" })],
    ["an extra field", frame({ event: "turn.started", turn_id: "t", debug: "x" })],
    ["an SSE name that does not match the payload", frame({ event: "turn.started", turn_id: "t" }, 1, "error")],
    ["progress before turn.started", frame({ event: "text.delta", text: "hi" })],
  ])("rejects %s as a protocol error", async (_name, body) => {
    const e = await failure(streamTurn(opts(fakeServer(body).fetch), fixtureRequest()));
    expect(e.kind).toBe("protocol");
    expect(errorTopic(e)).toBe("protocol");
  });

  it("rejects a second turn.started and a completion for another turn", async () => {
    const twice = sse([fixtureEvents[0]!, fixtureEvents[0]!]);
    expect((await failure(streamTurn(opts(fakeServer(twice).fetch), fixtureRequest()))).kind).toBe("protocol");
    const other = sse([{ event: "turn.started", turn_id: "turn_other" }, fixtureEvents.at(-1)!]);
    expect((await failure(streamTurn(opts(fakeServer(other).fetch), fixtureRequest()))).kind).toBe("protocol");
  });

  it("stops at the first terminal event: a duplicate turn.completed or a later error is never read", async () => {
    const completed = fixtureEvents.at(-1)!;
    const body = sse([...fixtureEvents, completed, { event: "error", code: "internal", message_key: "error.internal", retryable: false }]);
    await expect(streamTurn(opts(fakeServer(body).fetch), fixtureRequest())).resolves.toEqual(fixtureMessage);
  });

  it("turns an error event into a server error with its public code only", async () => {
    const body = sse([fixtureEvents[0]!, { event: "error", code: "upstream_timeout", message_key: "error.agent_timeout", retryable: true, request_id: "r1" }]);
    const e = await failure(streamTurn(opts(fakeServer(body).fetch), fixtureRequest()));
    expect([e.kind, e.retryable, e.info.code, errorTopic(e)]).toEqual(["server", true, "upstream_timeout", "timeout"]);
  });

  it.each([
    [jsonResponse(503, { error: { code: "upstream_unavailable", message_key: "error.agent_not_ready", retryable: true } }), "notReady", true],
    [jsonResponse(429, { error: { code: "rate_limited", message_key: "error.rate_limited", retryable: true } }), "rateLimited", true],
    [jsonResponse(422, { error: { code: "invalid_request", message_key: "error.invalid_request", retryable: false } }), "invalidContext", false],
    [jsonResponse(401, { error: { code: "invalid_request", message_key: "error.unauthorized", retryable: false } }), "unauthorized", false],
    [new Response("<html>Bad gateway</html>", { status: 502 }), "offline", true],
    [new Response("Service Unavailable", { status: 503 }), "notReady", true],
  ])("maps HTTP errors before the stream (%#)", async (response, topic, retryable) => {
    const e = await failure(streamTurn(opts(fakeServer(response).fetch), fixtureRequest()));
    expect([e.kind, errorTopic(e), e.retryable]).toEqual(["http", topic, retryable]);
  });

  it("reports a refused connection as offline", async () => {
    const e = await failure(streamTurn(opts(fakeServer(new TypeError("Failed to fetch")).fetch), fixtureRequest()));
    expect([e.kind, errorTopic(e), e.retryable]).toEqual(["network", "offline", true]);
  });

  it("reports a stream that ends without a terminal event as an interrupted connection", async () => {
    const e = await failure(streamTurn(opts(fakeServer(sse(fixtureEvents.slice(0, 3))).fetch), fixtureRequest()));
    expect([e.kind, e.retryable]).toEqual(["network", true]);
  });

  it("rejects a non-SSE 200 response", async () => {
    const e = await failure(streamTurn(opts(fakeServer(jsonResponse(200, fixtureMessage)).fetch), fixtureRequest()));
    expect(e.kind).toBe("protocol");
  });

  it("aborts with the caller's reason and stops reading", async () => {
    const s = manualStream();
    const controller = new AbortController();
    const p = streamTurn(opts(s.fetch), fixtureRequest(), {}, controller.signal);
    await vi.waitFor(() => expect(() => s.push(frame(fixtureEvents[0]!))).not.toThrow());
    controller.abort("field_changed");
    const e = await failure(p);
    expect([e.kind, e.info.abortReason, errorTopic(e)]).toEqual(["aborted", "field_changed", "fieldChanged"]);
    expect(s.aborted).toBe(true);
  });

  it("gives up on a silent stream (no bytes, not even keep-alives)", async () => {
    const s = manualStream();
    const e = await failure(streamTurn(opts(s.fetch, { stallMs: 30 }), fixtureRequest()));
    expect([e.kind, e.info.detail, e.retryable]).toEqual(["timeout", "stall", true]);
  });

  it("enforces an overall deadline even while keep-alives arrive", async () => {
    const s = manualStream();
    const p = streamTurn(opts(s.fetch, { deadlineMs: 80, stallMs: 1000 }), fixtureRequest());
    const ping = setInterval(() => s.push(": ping\n\n"), 10);
    const e = await failure(p);
    clearInterval(ping);
    expect([e.kind, e.info.detail]).toEqual(["timeout", "deadline"]);
  });

  it("refuses to send a request that breaks the AgentRequest contract", async () => {
    const server = fakeServer(sse(fixtureEvents));
    const bad = { ...fixtureRequest(), history: Array.from({ length: 11 }, () => ({ role: "user" as const, text: "x" })) };
    const e = await failure(streamTurn(opts(server.fetch), bad));
    expect(errorTopic(e)).toBe("invalidContext");
    expect(server.requests).toHaveLength(0);
  });

  it("passes host headers through but never invents credentials", async () => {
    const server = fakeServer(sse(fixtureEvents));
    await streamTurn(opts(server.fetch, { headers: () => ({ "X-CSRF-Token": "t1" }) }), fixtureRequest());
    expect(server.requests[0]!.headers["x-csrf-token"]).toBe("t1");
    expect(server.requests[0]!.headers.authorization).toBeUndefined();
  });
});

describe("sendMessage and checkBackend", () => {
  it("returns a validated AgentMessage from the JSON endpoint", async () => {
    const server = fakeServer(jsonResponse(200, fixtureMessage));
    await expect(sendMessage(opts(server.fetch), fixtureRequest())).resolves.toEqual(fixtureMessage);
    expect(server.requests[0]!.url).toBe("/agent/v1/agent/message");
  });

  it("rejects a JSON body that is not an AgentMessage", async () => {
    const e = await failure(sendMessage(opts(fakeServer(jsonResponse(200, { blocks: [] })).fetch), fixtureRequest()));
    expect(e.kind).toBe("protocol");
  });

  it("reads readiness, agent kind and knowledge mode; offline when unreachable", async () => {
    const ready = (async () => jsonResponse(200, { status: "ready", agent: "llm/scripted-llm", knowledge: { mode: "lexical" } })) as typeof fetch;
    await expect(checkBackend(opts(ready))).resolves.toEqual({ state: "ready", agent: "llm/scripted-llm", knowledge: "lexical", simulation: null });
    const withSim = (async () =>
      jsonResponse(200, {
        status: "ready", agent: "llm/scripted-llm",
        integrations: { simulation: { configured: "http", connected: true, service_mode: "stub", compare_rotations: true } },
      })) as typeof fetch;
    expect((await checkBackend(opts(withSim))).simulation).toEqual({ mode: "stub" });
    // Task 14: the real model is named only when the service reports one
    const gemini = (async () => jsonResponse(200, { status: "ready", agent: "llm/gemini", model: "gemini-3.8-flash" })) as typeof fetch;
    expect((await checkBackend(opts(gemini))).model).toBe("gemini-3.8-flash");
    expect((await checkBackend(opts(ready))).model).toBeUndefined();
    const simDown = (async () =>
      jsonResponse(200, { status: "ready", agent: "x", integrations: { simulation: { configured: "http", connected: false } } })) as typeof fetch;
    expect((await checkBackend(opts(simDown))).simulation).toBeNull();
    const down = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    await expect(checkBackend(opts(down))).resolves.toEqual({ state: "offline", agent: null, knowledge: null, simulation: null });
    const starting = (async () => jsonResponse(503, { status: "not_ready" })) as typeof fetch;
    expect((await checkBackend(opts(starting))).state).toBe("not_ready");
  });
});

describe("fixture sanity", () => {
  it("uses the M0 events fixture as a complete turn", () => {
    const events: AgentEvent[] = turn(fixtureMessage);
    expect(events.at(-1)?.event).toBe("turn.completed");
  });
});
