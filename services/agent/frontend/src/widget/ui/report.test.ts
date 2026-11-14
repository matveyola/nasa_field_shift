import { describe, expect, it } from "vitest";
import { fixtureMessage } from "../../test/helpers";
import { dictionaries } from "../i18n/strings";
import type { Item } from "../state/conversation";
import { buildReport, markdownToHtml } from "./report";

const answer = (over: Partial<Extract<Item, { kind: "assistant" }>> = {}): Item => ({
  kind: "assistant", id: "t1", question: "q", fieldId: "demo-poltava-01", validRevisions: [3], status: "completed",
  startedAt: 0, tools: [], draft: "", discardedDraft: false, message: fixtureMessage, error: null, actions: {}, constraints: {},
  ...over,
});

describe("report for the agronomist", () => {
  it("is one self-contained page with the answers, every value's origin, and escaped text", () => {
    const html = buildReport(
      [{ kind: "user", id: "u1", text: "<script>alert(1)</script> What about my field?" }, answer()],
      { d: dictionaries.uk, locale: "uk", name: "Польовий помічник", fieldName: "Демо-поле під Полтавою", now: new Date("2026-11-10T12:00:00Z") },
    );
    expect(html).toContain("<title>Звіт помічника: Демо-поле під Полтавою</title>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("Використані дані"); // the facts table
    expect(html).toContain("SPL4SMGP"); // the exact product of the soil-moisture value
    expect(html).toContain("Демо-дані"); // demo data stays marked
    expect(html).not.toMatch(/<script|\son\w+=/i); // no scripts or event attributes in the saved page
  });

  it("turns the agent's small markdown into safe HTML", () => {
    expect(markdownToHtml("**Ґрунт** <b>x</b>\n\n- one\n- two")).toBe("<p><strong>Ґрунт</strong> &lt;b&gt;x&lt;/b&gt;</p>\n<ul><li>one</li><li>two</li></ul>");
    // a line of text that goes on as a list, and _italic_ notes
    expect(markdownToHtml("_Демо-режим._\nОсь що відомо:\n- pH: 6,9\n- глина")).toBe(
      "<p><em>Демо-режим.</em><br>Ось що відомо:</p><ul><li>pH: 6,9</li><li>глина</li></ul>",
    );
    expect(markdownToHtml("field_id and snake_case_words stay")).toBe("<p>field_id and snake_case_words stay</p>");
  });
});
