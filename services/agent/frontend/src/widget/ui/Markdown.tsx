import { Fragment, type ReactNode } from "react";

/**
 * Safe Markdown for agent answers: paragraphs, bullet and numbered lists, short headings,
 * **bold**, *italic*, `code`, and `[n]` citation markers. Everything becomes React text nodes,
 * so model or source text can never inject HTML. Links are deliberately NOT rendered: the
 * service rejects URLs in model prose, and source links come only from Citation objects.
 */

export type CiteRenderer = (id: string, key: string) => ReactNode | null;

const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\s][^*\n]*\*|_[^_\s][^_\n]*_|\[\d{1,3}\](?!\()[.,;:!?)»”"]*)/g;

function inline(text: string, cite: CiteRenderer, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  // Markdown links: keep the visible text, drop the target.
  text = text.replace(/\[([^\]\n]+)\]\(([^)\n]*)\)/g, "$1");
  for (const m of text.matchAll(INLINE)) {
    const token = m[0];
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${keyPrefix}.${i++}`;
    if (token.startsWith("**")) out.push(<strong key={key}>{inline(token.slice(2, -2), cite, key)}</strong>);
    else if (token.startsWith("`")) out.push(<code key={key}>{token.slice(1, -1)}</code>);
    else if (token.startsWith("*") || token.startsWith("_")) out.push(<em key={key}>{inline(token.slice(1, -1), cite, key)}</em>);
    else {
      const [, id, punct] = /^\[(\d{1,3})\](.*)$/.exec(token)!;
      const node = cite(id!, key);
      // unknown [n]: plain text, never a button. A known one keeps its trailing punctuation on the same line.
      out.push(node == null ? token : punct ? <span key={key} className="fsa-nowrap">{node}{punct}</span> : node);
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "h"; text: string }
  | { kind: "quote"; lines: string[] };

function blocks(markdown: string): Block[] {
  const out: Block[] = [];
  for (const raw of markdown.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    const prev = out[out.length - 1];
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d{1,3}[.)]\s+(.*)$/.exec(line);
    const heading = /^#{1,6}\s+(.*)$/.exec(line);
    const quote = /^>\s?(.*)$/.exec(line);
    if (!line.trim()) out.push({ kind: "p", lines: [] }); // paragraph break
    else if (bullet) prev?.kind === "ul" ? prev.items.push(bullet[1]!) : out.push({ kind: "ul", items: [bullet[1]!] });
    else if (numbered) prev?.kind === "ol" ? prev.items.push(numbered[1]!) : out.push({ kind: "ol", items: [numbered[1]!] });
    else if (heading) out.push({ kind: "h", text: heading[1]! });
    else if (quote) prev?.kind === "quote" ? prev.lines.push(quote[1]!) : out.push({ kind: "quote", lines: [quote[1]!] });
    else if (prev?.kind === "p" && prev.lines.length) prev.lines.push(line);
    else out.push({ kind: "p", lines: [line] });
  }
  return out.filter((b) => b.kind !== "p" || b.lines.length);
}

export function Markdown({ text, cite }: { text: string; cite: CiteRenderer }) {
  return (
    <>
      {blocks(text).map((b, n) => {
        const k = `b${n}`;
        switch (b.kind) {
          case "p":
            return (
              <p key={k}>
                {b.lines.map((l, j) => (
                  <Fragment key={j}>
                    {j > 0 && <br />}
                    {inline(l, cite, `${k}.${j}`)}
                  </Fragment>
                ))}
              </p>
            );
          case "h":
            return (
              <p key={k} className="fsa-md-heading">
                <strong>{inline(b.text, cite, k)}</strong>
              </p>
            );
          case "quote":
            return <blockquote key={k}>{inline(b.lines.join(" "), cite, k)}</blockquote>;
          default: {
            const List = b.kind;
            return (
              <List key={k}>
                {b.items.map((item, j) => (
                  <li key={j}>{inline(item, cite, `${k}.${j}`)}</li>
                ))}
              </List>
            );
          }
        }
      })}
    </>
  );
}
