/**
 * Incremental Server-Sent Events parser (WHATWG HTML § 9.2 "Parsing an event stream").
 *
 * Bytes go in as they arrive from `fetch()`; whole events come out. Handles UTF-8 sequences
 * split across chunks, CRLF / LF / CR line endings (including a CR at the end of one chunk and
 * its LF at the start of the next), multi-line `data:`, comments and `id:`.
 */

export interface SSEFrame {
  event: string; // "message" when the frame has no event: field
  data: string;
  id: string | null; // last event id seen so far (persists across frames, per spec)
}

export class SSEParser {
  private decoder = new TextDecoder("utf-8");
  private buffer = "";
  private pendingCR = false;
  private data: string[] = [];
  private eventType = "";
  private lastId: string | null = null;

  /** Feed a chunk; returns the frames completed by it. */
  push(chunk: Uint8Array): SSEFrame[] {
    return this.feed(this.decoder.decode(chunk, { stream: true }));
  }

  /** End of input: flushes the decoder. An unterminated last frame is discarded, as the spec says. */
  end(): SSEFrame[] {
    const frames = this.feed(this.decoder.decode());
    this.buffer = "";
    this.data = [];
    this.eventType = "";
    return frames;
  }

  private feed(text: string): SSEFrame[] {
    if (this.pendingCR && text.startsWith("\n")) text = text.slice(1); // CRLF split across chunks
    this.pendingCR = false;
    this.buffer += text;
    const frames: SSEFrame[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      const line = this.buffer.slice(start, i);
      if (ch === "\r") {
        if (i + 1 === this.buffer.length) this.pendingCR = true;
        else if (this.buffer[i + 1] === "\n") i++;
      }
      start = i + 1;
      const frame = this.line(line);
      if (frame) frames.push(frame);
    }
    this.buffer = this.buffer.slice(start);
    return frames;
  }

  private line(line: string): SSEFrame | null {
    if (line === "") {
      // Blank line: dispatch. A frame without data (e.g. after only an id) is not an event.
      const frame = this.data.length ? { event: this.eventType || "message", data: this.data.join("\n"), id: this.lastId } : null;
      this.data = [];
      this.eventType = "";
      return frame;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "data":
        this.data.push(value);
        break;
      case "event":
        this.eventType = value;
        break;
      case "id":
        if (!value.includes("\0")) this.lastId = value;
        break;
      default: // "retry" and unknown fields: ignored (we never reconnect a POST turn automatically)
    }
    return null;
  }
}
