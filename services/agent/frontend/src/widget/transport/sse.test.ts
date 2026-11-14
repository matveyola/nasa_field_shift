import { describe, expect, it } from "vitest";
import { SSEParser } from "./sse";

const enc = new TextEncoder();
const feed = (p: SSEParser, ...parts: (string | Uint8Array)[]) =>
  parts.flatMap((x) => p.push(typeof x === "string" ? enc.encode(x) : x));

describe("SSEParser", () => {
  it("parses one frame with event, id and data", () => {
    expect(feed(new SSEParser(), 'event: turn.started\nid: 1\ndata: {"a":1}\n\n')).toEqual([{ event: "turn.started", data: '{"a":1}', id: "1" }]);
  });

  it("joins a frame split across many chunks, even mid-field-name", () => {
    const p = new SSEParser();
    expect(feed(p, "ev", "ent: x\nda", "ta: hel", "lo\n")).toEqual([]);
    expect(feed(p, "\n")).toEqual([{ event: "x", data: "hello", id: null }]);
  });

  it("returns several frames from one chunk", () => {
    const frames = feed(new SSEParser(), "data: a\n\ndata: b\n\nevent: e\ndata: c\n\n");
    expect(frames.map((f) => [f.event, f.data])).toEqual([["message", "a"], ["message", "b"], ["e", "c"]]);
  });

  it("decodes a UTF-8 character split between chunks", () => {
    const bytes = enc.encode("data: Поле ✓\n\n");
    const cut = 8; // inside the two-byte "о"
    const p = new SSEParser();
    expect(feed(p, bytes.slice(0, cut))).toEqual([]);
    expect(feed(p, bytes.slice(cut))).toEqual([{ event: "message", data: "Поле ✓", id: null }]);
  });

  it("decodes a 4-byte character delivered one byte at a time", () => {
    const bytes = enc.encode("data: 🌾\n\n");
    const p = new SSEParser();
    const out = [...bytes].flatMap((b) => p.push(Uint8Array.of(b)));
    expect(out[0]?.data).toBe("🌾");
  });

  it("joins multi-line data with newlines", () => {
    expect(feed(new SSEParser(), "data: line 1\ndata: line 2\ndata:\n\n")[0]?.data).toBe("line 1\nline 2\n");
  });

  it("ignores comments and keep-alives, and frames without data", () => {
    expect(feed(new SSEParser(), ": ping\n\n:keep-alive\n\nid: 7\n\nretry: 100\n\n")).toEqual([]);
  });

  it("accepts CRLF, CR and LF line endings, including a CRLF split across chunks", () => {
    const p = new SSEParser();
    expect(feed(p, "data: a\r\n\r\ndata: b\r\rdata: c\r")).toHaveLength(2);
    expect(feed(p, "\n\r\n").map((f) => f.data)).toEqual(["c"]);
  });

  it("keeps the last event id across frames, and strips only one leading space", () => {
    const frames = feed(new SSEParser(), "id: 5\ndata:  two spaces\n\ndata: next\n\n");
    expect(frames.map((f) => [f.id, f.data])).toEqual([["5", " two spaces"], ["5", "next"]]);
  });

  it("discards an unterminated last frame at end of stream", () => {
    const p = new SSEParser();
    feed(p, "data: half");
    expect(p.end()).toEqual([]);
  });
});
