import type { ErrorBody } from "../contracts";

/**
 * Every way a turn can fail, as one type the UI can localise. Never carries response bodies,
 * stack traces or headers: only the contract's public `code` / `message_key`.
 */
export type AgentErrorKind =
  | "network" // fetch failed or the connection dropped mid-stream
  | "http" // non-2xx before the stream started (ErrorEnvelope when the service sent one)
  | "server" // the stream ended with an `error` event
  | "timeout" // client deadline or a stream that went silent (no bytes, not even keep-alives)
  | "protocol" // invalid JSON, schema mismatch, unknown event, or end of stream without a terminal event
  | "aborted"; // cancelled by the user, a field switch, or closing the widget

export type AbortReason = "user" | "field_changed" | "unmounted";

export class AgentError extends Error {
  constructor(
    readonly kind: AgentErrorKind,
    readonly retryable: boolean,
    readonly info: {
      status?: number;
      code?: ErrorBody["code"];
      messageKey?: string;
      requestId?: string | null;
      abortReason?: AbortReason;
      detail?: string; // developer detail for logs only; never rendered
    } = {},
  ) {
    super(`${kind}${info.code ? `:${info.code}` : ""}${info.status ? `:${info.status}` : ""}`);
    this.name = "AgentError";
  }
}

/** Which user-facing explanation fits this error (keys of the `errors` dictionary). */
export type ErrorTopic =
  | "offline"
  | "notReady"
  | "timeout"
  | "rateLimited"
  | "invalidContext"
  | "unauthorized"
  | "tooLarge"
  | "knowledgeMissing"
  | "modelError"
  | "protocol"
  | "cancelled"
  | "fieldChanged"
  | "generic";

export function errorTopic(e: AgentError): ErrorTopic {
  if (e.kind === "aborted") return e.info.abortReason === "field_changed" ? "fieldChanged" : "cancelled";
  if (e.kind === "network") return "offline";
  if (e.kind === "timeout") return "timeout";
  if (e.kind === "protocol") return e.info.code === "invalid_request" ? "invalidContext" : "protocol";
  if (e.info.status === 401) return "unauthorized";
  if (e.info.status === 413 || e.info.code === "payload_too_large") return "tooLarge";
  if (e.info.status === 422 || (e.kind === "http" && e.info.code === "invalid_request")) return "invalidContext";
  if (e.info.messageKey?.includes("knowledge")) return "knowledgeMissing";
  switch (e.info.code) {
    case "rate_limited":
      return "rateLimited";
    case "upstream_timeout":
      return "timeout";
    case "upstream_unavailable":
      return "notReady";
    case "llm_error":
      return "modelError";
  }
  // 5xx without the service's ErrorEnvelope came from a proxy: 502/504 mean it could not reach the service.
  if (e.kind === "http" && !e.info.code && (e.info.status === 502 || e.info.status === 504)) return "offline";
  if (e.kind === "http" && !e.info.code && (e.info.status ?? 0) >= 500) return "notReady";
  return "generic";
}
