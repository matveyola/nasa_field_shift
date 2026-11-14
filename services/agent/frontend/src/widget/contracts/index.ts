// The widget speaks only the M0 contracts: generated types and generated runtime validators.
export type * from "../../../../contracts/ts/index";
export type { ComparisonResponse } from "../../../../contracts/ts/integration";
import type { AgentEvent, ErrorEnvelope, UIActionEnvelope } from "../../../../contracts/ts/index";
import type { ComparisonResponse } from "../../../../contracts/ts/integration";
import {
  validateAgentEvent,
  validateComparisonResponse,
  validateAgentRequest,
  validateErrorEnvelope,
  validateFieldContext,
  validateUIActionEnvelope,
  type Validator,
} from "./validators.js";

function describe(v: Validator): string {
  const e = v.errors?.[0];
  return e ? `${e.instancePath || "/"} ${e.message ?? e.keyword}` : "invalid";
}

/** Returns the event if it matches the M0 AgentEvent schema, otherwise a short reason. */
export function checkAgentEvent(data: unknown): { ok: true; event: AgentEvent } | { ok: false; reason: string } {
  return validateAgentEvent(data) ? { ok: true, event: data as AgentEvent } : { ok: false, reason: describe(validateAgentEvent) };
}

export function isErrorEnvelope(data: unknown): data is ErrorEnvelope {
  return validateErrorEnvelope(data);
}

export function isUIActionEnvelope(data: unknown): data is UIActionEnvelope {
  return validateUIActionEnvelope(data);
}

/** A simulation result fetched by the host (proposed integration contract v0.1). */
export function checkComparisonResponse(data: unknown): { ok: true; value: ComparisonResponse } | { ok: false; reason: string } {
  return validateComparisonResponse(data)
    ? { ok: true, value: data as ComparisonResponse }
    : { ok: false, reason: describe(validateComparisonResponse) };
}

export function requestProblem(data: unknown): string | null {
  return validateAgentRequest(data) ? null : describe(validateAgentRequest);
}

export function fieldContextProblem(data: unknown): string | null {
  return validateFieldContext(data) ? null : describe(validateFieldContext);
}
