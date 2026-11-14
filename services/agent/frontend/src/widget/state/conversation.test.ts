import { describe, expect, it } from "vitest";
import { fixtureMessage } from "../../test/helpers";
import { AgentError } from "../transport/errors";
import { type AssistantItem, buildHistory, type Item, proposedConstraints, reducer } from "./conversation";

function assistant(id: string, over: Partial<AssistantItem> = {}): AssistantItem {
  return {
    kind: "assistant",
    id,
    question: "q",
    fieldId: "f1",
    validRevisions: [1],
    status: "streaming",
    startedAt: 0,
    tools: [],
    draft: "",
    discardedDraft: false,
    message: null,
    error: null,
    actions: {},
    constraints: {},
    ...over,
  };
}

describe("reducer", () => {
  const start = reducer({ threads: {} }, { type: "start", fieldId: "f1", user: { kind: "user", id: "u1", text: "hi" }, assistant: assistant("t1") });

  it("keeps one conversation per field", () => {
    const other = reducer(start, { type: "start", fieldId: "f2", user: { kind: "user", id: "u2", text: "yo" }, assistant: assistant("t2", { fieldId: "f2" }) });
    expect(other.threads.f1).toHaveLength(2);
    expect(other.threads.f2?.map((i) => i.id)).toEqual(["u2", "t2"]);
  });

  it("accumulates deltas and tool steps, then replaces the draft with the final message", () => {
    let s = reducer(start, { type: "event", fieldId: "f1", itemId: "t1", event: { event: "tool.started", call_id: "c1", tool: "search_knowledge" } });
    s = reducer(s, { type: "event", fieldId: "f1", itemId: "t1", event: { event: "text.delta", text: "Draft " } });
    s = reducer(s, { type: "event", fieldId: "f1", itemId: "t1", event: { event: "text.delta", text: "text" } });
    expect((s.threads.f1![1] as AssistantItem).draft).toBe("Draft text");
    s = reducer(s, { type: "complete", fieldId: "f1", itemId: "t1", message: fixtureMessage });
    const item = s.threads.f1![1] as AssistantItem;
    expect([item.status, item.draft, item.message]).toEqual(["completed", "", fixtureMessage]);
    expect(item.tools[0]!.status).toBe("error"); // started, never finished
  });

  it("records exactly one outcome per turn; late events are ignored", () => {
    let s = reducer(start, { type: "complete", fieldId: "f1", itemId: "t1", message: fixtureMessage });
    s = reducer(s, { type: "fail", fieldId: "f1", itemId: "t1", error: new AgentError("network", true) });
    s = reducer(s, { type: "event", fieldId: "f1", itemId: "t1", event: { event: "text.delta", text: "late" } });
    const item = s.threads.f1![1] as AssistantItem;
    expect([item.status, item.error, item.draft]).toEqual(["completed", null, ""]);
  });
});

describe("buildHistory", () => {
  const done = (id: string) => assistant(id, { status: "completed", message: fixtureMessage });

  it("uses completed turns only, with citation markers removed", () => {
    const thread: Item[] = [
      { kind: "user", id: "u1", text: "first" },
      done("a1"),
      { kind: "user", id: "u2", text: "failed one" },
      assistant("a2", { status: "failed" }),
    ];
    const h = buildHistory(thread);
    expect(h.map((t) => t.role)).toEqual(["user", "assistant"]);
    expect(h[1]!.text).not.toMatch(/\[\d\]/);
  });

  it("keeps at most 10 entries, newest last, starting with a user turn", () => {
    const thread: Item[] = Array.from({ length: 8 }, (_, i) => [{ kind: "user", id: `u${i}`, text: `q${i}` } as Item, done(`a${i}`)]).flat();
    const h = buildHistory(thread);
    expect(h).toHaveLength(10);
    expect(h[0]).toEqual({ role: "user", text: "q3" });
    expect(h.at(-1)!.role).toBe("assistant");
  });

  it("drops the oldest turns when the history would be too large", () => {
    const thread: Item[] = Array.from({ length: 4 }, (_, i) => [{ kind: "user", id: `u${i}`, text: "x".repeat(15_000) } as Item, done(`a${i}`)]).flat();
    expect(buildHistory(thread).reduce((n, t) => n + t.text.length, 0)).toBeLessThanOrEqual(24_000);
  });
});

describe("proposedConstraints", () => {
  it("merges constraints_proposed and set_constraints actions without duplicates", () => {
    expect(proposedConstraints(fixtureMessage).map((c) => c.id)).toEqual(["k_irrigation", "k_fertilizer"]);
  });
});
