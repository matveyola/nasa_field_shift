import type { Fact, MessageBlock } from "../contracts";
import { formatQuantity } from "../i18n/format";
import { type Dict, fmt, label, type Locale } from "../i18n/strings";
import type { AssistantItem, Item } from "../state/conversation";

/* "Report for the agronomist": the conversation as one self-contained, printable HTML page (no scripts, no external
   files). Everything in it is already in the widget: the answers, every value with its origin, period and exact
   product, and the cited passages. Nothing is sent anywhere; the farmer saves or prints it. */

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** The small markdown the agent writes (paragraphs, "- " lists, **bold**), as safe HTML. */
export function markdownToHtml(md: string): string {
  const inline = (t: string) =>
    esc(t)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])[_*]([^_*\n]+?)[_*](?=$|[\s).,:;!?])/g, "$1<em>$2</em>");
  const item = /^\s*[-*]\s+/;
  return md
    .split(/\n{2,}/)
    .map((para) => {
      // a paragraph may start with a line of text and go on as a list: text lines and list runs, in order
      const out: string[] = [];
      let text: string[] = [];
      let list: string[] = [];
      const flush = () => {
        if (text.length) out.push(`<p>${text.map(inline).join("<br>")}</p>`);
        if (list.length) out.push(`<ul>${list.map((l) => `<li>${inline(l)}</li>`).join("")}</ul>`);
        text = [];
        list = [];
      };
      for (const line of para.split("\n")) {
        if (item.test(line)) {
          if (text.length) { out.push(`<p>${text.map(inline).join("<br>")}</p>`); text = []; }
          list.push(line.replace(item, ""));
        } else {
          if (list.length) { out.push(`<ul>${list.map((l) => `<li>${inline(l)}</li>`).join("")}</ul>`); list = []; }
          text.push(line);
        }
      }
      flush();
      return out.join("");
    })
    .join("\n");
}

function value(f: Fact, d: Dict, locale: Locale): string {
  return f.quantity ? formatQuantity(f.quantity, locale) : (f.category ?? d.evidence.noValue);
}

function blockHtml(b: MessageBlock, facts: Map<string, Fact>, d: Dict, locale: Locale): string {
  const v = (id: string | null | undefined) => {
    const f = id ? facts.get(id) : undefined;
    return f ? esc(value(f, d, locale)) : "—";
  };
  const title = (t?: string | null) => (t ? `<h4>${esc(t)}</h4>` : "");
  const scenario = "scenario" in b && b.scenario ? `<p class="note">${esc(fmt(d.blocks.simulation, { name: d.blocks.scenario[b.scenario] ?? b.scenario }))}</p>` : "";
  switch (b.type) {
    case "metrics":
      return `${title(b.title)}<table><tbody>${b.fact_ids.map((id) => `<tr><th>${esc(facts.has(id) ? label(d, facts.get(id)!.label_key) : id)}</th><td>${v(id)}</td></tr>`).join("")}</tbody></table>${scenario}`;
    case "table":
      return `${title(b.title)}<table><thead><tr><td></td>${b.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${b.rows
        .map((r) => `<tr><th>${esc(r.label)}</th>${r.fact_ids.map((id) => `<td>${v(id)}</td>`).join("")}</tr>`)
        .join("")}</tbody></table>${scenario}`;
    case "bar_chart":
      return `${title(b.title)}<table><tbody>${b.bars.map((x) => `<tr><th>${esc(x.label)}</th><td>${v(x.fact_id)}</td></tr>`).join("")}</tbody></table>${scenario}`;
    case "callout":
      return `<p class="callout">${esc(b.text)}</p>`;
    case "clarifying_question":
      return `<p>${esc(b.text)}</p>`;
    default:
      return "";
  }
}

function answerHtml(item: AssistantItem, d: Dict, locale: Locale): string {
  const m = item.message;
  if (!m) return `<p class="note">${esc(d.report.noAnswer)}</p>`;
  const facts = new Map((m.facts ?? []).map((f) => [f.id, f]));
  const parts = m.blocks.map((b) => (b.type === "text" ? markdownToHtml(b.markdown) : blockHtml(b, facts, d, locale)));
  if (m.warnings?.length) parts.push(`<ul class="warnings">${m.warnings.map((w) => `<li>${esc(d.warnings[w.code])}</li>`).join("")}</ul>`);
  if (m.facts?.length) {
    parts.push(`<h4>${esc(d.evidence.heading)}</h4><table class="facts"><thead><tr><th>${esc(d.report.what)}</th><th>${esc(d.report.value)}</th><th>${esc(d.report.origin)}</th><th>${esc(d.report.source)}</th><th>${esc(d.report.period)}</th></tr></thead><tbody>${m.facts
      .map((f) => {
        const p = f.provenance;
        const source = p.source ? `${p.source.provider} · ${p.source.product_id} ${p.source.version}` : p.model ? `${p.model.name} ${p.model.version}` : (p.method ?? "");
        const origin = [d.evidence.kind[p.kind], p.data_status !== "live" ? d.evidence.status[p.data_status] : ""].filter(Boolean).join(" · ");
        const period = p.temporal ? fmt(d.evidence.period, { start: p.temporal.start, end: p.temporal.end }) : "";
        return `<tr><td>${esc(label(d, f.label_key))}</td><td>${esc(value(f, d, locale))}</td><td>${esc(origin)}</td><td>${esc(source)}</td><td>${esc(period)}</td></tr>`;
      })
      .join("")}</tbody></table>`);
  }
  if (m.citations?.length) {
    parts.push(`<h4>${esc(fmt(d.sources.heading, { n: m.citations.length }))}</h4><ol class="sources">${m.citations
      .map((c) => {
        const where = [c.locator.chapter && fmt(d.sources.chapter, { v: c.locator.chapter }), c.locator.section, c.locator.table, c.locator.page && fmt(d.sources.page, { v: c.locator.page })].filter(Boolean).join(" · ");
        return `<li value="${esc(c.id)}"><strong>${esc(c.title)}</strong> (${esc(c.publisher)}, ${c.year})${where ? ` · ${esc(where)}` : ""}<blockquote>${esc(c.quote)}</blockquote>${/^https?:\/\//.test(c.url) ? `<a href="${esc(c.url)}">${esc(c.url)}</a>` : ""}</li>`;
      })
      .join("")}</ol>`);
  }
  return parts.join("\n");
}

export function buildReport(thread: Item[], opts: { d: Dict; locale: Locale; name: string; fieldName: string; now: Date }): string {
  const { d, locale, name, fieldName, now } = opts;
  const when = now.toLocaleString(locale === "uk" ? "uk-UA" : "en-GB", { dateStyle: "long", timeStyle: "short" });
  const turns = thread
    .map((it) =>
      it.kind === "user"
        ? `<h3>${esc(d.you)}: ${esc(it.text)}</h3>`
        : `<div class="answer"><p class="who">${esc(name)}</p>${answerHtml(it, d, locale)}</div>`,
    )
    .join("\n");
  return `<!doctype html>
<html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(fmt(d.report.title, { field: fieldName }))}</title>
<style>
  body { font: 15px/1.55 "Onest Variable", "Onest", "Segoe UI", system-ui, sans-serif; color: #23261f; background: #fff; max-width: 820px; margin: 0 auto; padding: 32px 20px 60px; }
  h1 { font-size: 24px; margin: 0 0 4px; } h3 { font-size: 16px; margin: 28px 0 8px; padding-top: 16px; border-top: 1px solid #d9cfc1; }
  h4 { font-size: 13px; margin: 14px 0 6px; color: #5e625a; } .meta, .note, .who { color: #5e625a; font-size: 13px; } .who { margin: 0 0 4px; font-weight: 600; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 4px 0 8px; font-variant-numeric: tabular-nums; }
  th, td { border: 1px solid #d9cfc1; padding: 5px 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; } thead th { background: #f3eee6; }
  blockquote { margin: 4px 0; padding-left: 10px; border-left: 3px solid #d9cfc1; color: #3d4038; } .callout { border-left: 3px solid #a3502b; padding-left: 10px; }
  .warnings { color: #8c5e0a; font-size: 13px; } a { color: #2f5d34; word-break: break-all; }
  @media print { body { padding: 0; } h3 { break-after: avoid; } table, blockquote { break-inside: avoid; } }
</style></head><body>
<h1>${esc(fmt(d.report.title, { field: fieldName }))}</h1>
<p class="meta">${esc(fmt(d.report.made, { name, when }))}</p>
<p class="note">${esc(d.report.disclaimer)}</p>
${turns}
</body></html>`;
}

/** Saves the report as an .html file the farmer can open, print or send (nothing leaves the device). */
export function downloadReport(html: string, now: Date): void {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `field-shift-report-${now.toISOString().slice(0, 10)}.html`;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
