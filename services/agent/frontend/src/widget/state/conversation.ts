import type { AgentEvent, AgentMessage, Constraint, HistoryTurn, ToolStartedEvent, UIState } from "../contracts";
import type { RejectReason } from "../actions/policy";
import type { AgentError } from "../transport/errors";

export interface ToolActivity {
  callId: string;
  tool: ToolStartedEvent["tool"];
  status: "running" | "ok" | "error" | "timeout";
}

export type ActionPhase =
  | { phase: "idle" }
  | { phase: "applying" }
  | { phase: "applied"; auto: boolean }
  | { phase: "rejected"; reason: RejectReason };

export type ConstraintPhase =
  | { phase: "proposed" }
  | { phase: "applying" }
  | { phase: "confirmed" }
  | { phase: "dismissed" }
  | { phase: "rejected"; reason: RejectReason };

export interface UserItem {
  kind: "user";
  id: string;
  text: string;
}

export interface AssistantItem {
  kind: "assistant";
  id: string;
  /** The question this turn answers, for "try again". */
  question: string;
  /** Field and revision the request was made for: actions are only valid there. */
  fieldId: string;
  validRevisions: number[];
  status: "streaming" | "completed" | "failed";
  startedAt: number;
  tools: ToolActivity[];
  /** `text.delta` so far. Typing effect only; replaced by the validated final message. */
  draft: string;
  /** Text was streamed but the turn failed: it was removed, and the farmer is told so. */
  discardedDraft: boolean;
  message: AgentMessage | null;
  error: AgentError | null;
  actions: Record<string, ActionPhase>;
  constraints: Record<string, ConstraintPhase>;
  /** The view before this answer's automatic changes, so the farmer can go back to it. */
  back?: BackState;
}

export interface BackState {
  before: UIState;
  phase: "ready" | "applying" | "done" | "failed";
}

export type Item = UserItem | AssistantItem;

export interface ConversationState {
  /** One conversation per field id. Nothing is shared between fields; nothing is persisted. */
  threads: Record<string, Item[]>;
}

export type ConversationAction =
  | { type: "start"; fieldId: string; user: UserItem; assistant: AssistantItem }
  | { type: "event"; fieldId: string; itemId: string; event: AgentEvent }
  | { type: "complete"; fieldId: string; itemId: string; message: AgentMessage }
  | { type: "fail"; fieldId: string; itemId: string; error: AgentError }
  | { type: "remove"; fieldId: string; itemIds: string[] }
  | { type: "actionPhase"; fieldId: string; itemId: string; actionId: string; phase: ActionPhase }
  | { type: "constraintPhase"; fieldId: string; itemId: string; constraintIds: string[]; phase: ConstraintPhase }
  | { type: "revision"; fieldId: string; itemId: string; revision: number }
  | { type: "back"; fieldId: string; itemId: string; back: BackState };

export const initialState: ConversationState = { threads: {} };

function patch(state: ConversationState, fieldId: string, itemId: string, fn: (item: AssistantItem) => AssistantItem): ConversationState {
  const thread = state.threads[fieldId];
  if (!thread) return state;
  let changed = false;
  const next = thread.map((it) => {
    if (it.kind !== "assistant" || it.id !== itemId) return it;
    const updated = fn(it);
    changed = updated !== it;
    return updated;
  });
  return changed ? { threads: { ...state.threads, [fieldId]: next } } : state;
}

export function reducer(state: ConversationState, a: ConversationAction): ConversationState {
  switch (a.type) {
    case "start":
      return { threads: { ...state.threads, [a.fieldId]: [...(state.threads[a.fieldId] ?? []), a.user, a.assistant] } };
    case "event":
      return patch(state, a.fieldId, a.itemId, (it) => {
        if (it.status !== "streaming") return it; // late events after an outcome are ignored
        const e = a.event;
        if (e.event === "text.delta") return { ...it, draft: it.draft + e.text };
        if (e.event === "tool.started") return { ...it, tools: [...it.tools, { callId: e.call_id, tool: e.tool, status: "running" }] };
        if (e.event === "tool.finished")
          return { ...it, tools: it.tools.map((t) => (t.callId === e.call_id ? { ...t, status: e.status } : t)) };
        return it;
      });
    case "complete":
      return patch(state, a.fieldId, a.itemId, (it) =>
        it.status !== "streaming"
          ? it // exactly one outcome per turn
          : { ...it, status: "completed", message: a.message, draft: "", tools: it.tools.map(settle) },
      );
    case "fail":
      return patch(state, a.fieldId, a.itemId, (it) =>
        it.status !== "streaming" ? it : { ...it, status: "failed", error: a.error, draft: "", discardedDraft: it.draft.length > 0, tools: it.tools.map(settle) },
      );
    case "remove": {
      const thread = state.threads[a.fieldId];
      if (!thread) return state;
      return { threads: { ...state.threads, [a.fieldId]: thread.filter((it) => !a.itemIds.includes(it.id)) } };
    }
    case "actionPhase":
      return patch(state, a.fieldId, a.itemId, (it) => ({ ...it, actions: { ...it.actions, [a.actionId]: a.phase } }));
    case "back":
      return patch(state, a.fieldId, a.itemId, (it) => ({ ...it, back: a.back }));
    case "constraintPhase":
      return patch(state, a.fieldId, a.itemId, (it) => {
        const constraints = { ...it.constraints };
        for (const id of a.constraintIds) constraints[id] = a.phase;
        return { ...it, constraints };
      });
    case "revision":
      return patch(state, a.fieldId, a.itemId, (it) =>
        it.validRevisions.includes(a.revision) ? it : { ...it, validRevisions: [...it.validRevisions, a.revision] },
      );
  }
}

function settle(t: ToolActivity): ToolActivity {
  return t.status === "running" ? { ...t, status: "error" } : t; // a tool without tool.finished did not complete
}

/** Constraints proposed by a message: `constraints_proposed` plus any inside `compare.set_constraints` actions. */
export function proposedConstraints(message: AgentMessage): Constraint[] {
  const all = [...(message.constraints_proposed ?? [])];
  for (const env of message.ui_actions ?? []) {
    if (env.action.type === "compare.set_constraints") all.push(...env.action.constraints);
  }
  const seen = new Set<string>();
  return all.filter((c) => c.status === "proposed" && !seen.has(c.id) && seen.add(c.id));
}

export function messageText(message: AgentMessage): string {
  return message.blocks
    // cards, tables and charts hold only fact references: nothing to quote back as text
    .map((b) => (b.type === "text" ? b.markdown : b.type === "clarifying_question" || b.type === "callout" ? b.text : ""))
    .filter(Boolean)
    .join("\n\n")
    .replace(/\s*\[\d{1,3}\]/g, ""); // citation markers mean nothing without their objects
}

const MAX_HISTORY = 10; // AgentRequest.history maxItems
const MAX_TURN_CHARS = 16_000; // HistoryTurn.text maxLength
/** Keeps requests well under the service's 256 KB body limit even with a large field context. */
const MAX_HISTORY_CHARS = 24_000;

/** History for the next request: completed turns of this field's conversation only, newest last. */
export function buildHistory(thread: readonly Item[]): HistoryTurn[] {
  const turns: HistoryTurn[] = [];
  for (let i = 0; i < thread.length; i++) {
    const user = thread[i];
    const reply = thread[i + 1];
    if (user?.kind !== "user" || reply?.kind !== "assistant" || reply.status !== "completed" || !reply.message) continue;
    turns.push({ role: "user", text: user.text.slice(0, MAX_TURN_CHARS) });
    turns.push({ role: "assistant", text: messageText(reply.message).slice(0, MAX_TURN_CHARS) || "…" });
  }
  let recent = turns.slice(-MAX_HISTORY);
  while (recent.reduce((n, t) => n + t.text.length, 0) > MAX_HISTORY_CHARS && recent.length > 2) recent = recent.slice(2);
  if (recent[0]?.role === "assistant") recent = recent.slice(1); // never start mid-turn
  return recent;
}
