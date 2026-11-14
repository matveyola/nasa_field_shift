// Minimal typed client for the Field Shift agent service (M1). Works in browsers and Node 18+.
// Type-checked by `npm run check:ts` against the generated M0 contract types.
import type { AgentEvent, AgentMessage, AgentRequest, ErrorEnvelope, UIActionEnvelope } from "../contracts/ts/index";

export interface AgentClientOptions {
  baseUrl: string; // e.g. "http://127.0.0.1:8000"
  apiKey?: string; // only when the service sets AGENT_API_KEY; never ship a real key in browser code
}

export class AgentHttpError extends Error {
  constructor(
    readonly status: number,
    readonly body: ErrorEnvelope | null,
  ) {
    super(body?.error.message_key ?? `HTTP ${status}`);
  }
}

function headers(opts: AgentClientOptions, request: AgentRequest, accept: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    Accept: accept,
    "X-Request-ID": request.request_id, // correlates our logs with the service's
    ...(opts.apiKey ? { Authorization: `Bearer ${opts.apiKey}` } : {}),
  };
}

async function failure(res: Response): Promise<AgentHttpError> {
  return new AgentHttpError(res.status, (await res.json().catch(() => null)) as ErrorEnvelope | null);
}

/** One turn, one JSON response. */
export async function sendMessage(opts: AgentClientOptions, request: AgentRequest, signal?: AbortSignal): Promise<AgentMessage> {
  const res = await fetch(`${opts.baseUrl}/v1/agent/message`, {
    method: "POST",
    headers: headers(opts, request, "application/json"),
    body: JSON.stringify(request),
    signal,
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as AgentMessage;
}

/**
 * One turn as a stream of AgentEvents (SSE over POST, so EventSource cannot be used).
 * Ends after `turn.completed` or `error`. Abort with `signal` when the user switches fields;
 * the service then stops the agent.
 */
export async function* streamTurn(
  opts: AgentClientOptions,
  request: AgentRequest,
  signal?: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${opts.baseUrl}/v1/agent/stream`, {
    method: "POST",
    headers: headers(opts, request, "text/event-stream"),
    body: JSON.stringify(request),
    signal,
  });
  // Errors before the stream starts (401, 413, 415, 422, 503) are plain JSON responses.
  if (!res.ok || !res.body) throw await failure(res);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value.replace(/\r\n?/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = frame
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(line.startsWith("data: ") ? 6 : 5))
          .join("\n");
        if (!data) continue; // ": ping" keep-alive comments carry no data
        const event = JSON.parse(data) as AgentEvent; // validate with Ajv in app code (see README)
        yield event;
        if (event.event === "turn.completed" || event.event === "error") return;
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
  throw new Error("stream closed without turn.completed or error (connection dropped)");
}

/**
 * Example consumer. UI actions are proposals: the host re-checks them against its live state
 * (`isStillValid`) and applies only what it allows. Nothing from the agent is executed as code.
 */
export async function runTurn(
  opts: AgentClientOptions,
  request: AgentRequest,
  ui: {
    onText(textSoFar: string): void;
    isStillValid(action: UIActionEnvelope): boolean; // e.g. revision matches, layer exists
    apply(action: UIActionEnvelope): void;
    showChip(action: UIActionEnvelope): void;
  },
  signal?: AbortSignal,
): Promise<AgentMessage> {
  let text = "";
  for await (const event of streamTurn(opts, request, signal)) {
    switch (event.event) {
      case "text.delta":
        text += event.text; // typing effect only; render the final message from turn.completed
        ui.onText(text);
        break;
      case "turn.completed":
        // The service always sends ui_actions; the generated type marks it optional because
        // the M0 schema is exported in validation mode (fields with defaults are optional).
        for (const action of event.message.ui_actions ?? []) {
          if (!ui.isStillValid(action)) continue;
          if (action.trigger === "auto") ui.apply(action);
          else ui.showChip(action);
        }
        return event.message;
      case "error":
        throw new Error(`${event.code}: ${event.message_key}${event.retryable ? " (retryable)" : ""}`);
    }
  }
  throw new Error("unreachable: streamTurn always ends with a terminal event or throws");
}
