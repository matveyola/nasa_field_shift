import type { BarChartBlock, CalloutBlock, Fact, MessageBlock, MetricsBlock, TableBlock } from "../contracts";
import { formatQuantity } from "../i18n/format";
import { fmt, label } from "../i18n/strings";
import { useEnv } from "./context";
import * as I from "./icons";

/* Presentation blocks: the agent sends fact ids only, so every value here is a fact of the message, drawn with its
   unit and origin. A block that names a fact the message does not carry is skipped (the server never sends one). */

type Facts = Map<string, Fact>;

function useValue() {
  const { d, locale } = useEnv();
  return (f: Fact) => (f.quantity ? formatQuantity(f.quantity, locale) : (f.category ?? d.evidence.noValue));
}

/** Where a value comes from, in two words: "Satellite data · Demo data". */
function Origin({ fact }: { fact: Fact }) {
  const { d } = useEnv();
  const p = fact.provenance;
  return (
    <span className="fsa-origin">
      {d.evidence.kind[p.kind]}
      {p.data_status !== "live" && <> · <span className={`fsa-origin-status fsa-origin-status--${p.data_status}`}>{d.evidence.status[p.data_status]}</span></>}
    </span>
  );
}

/** "−351,4 мм" -> the number large, the unit small after it (the tile's look); anything else stays one piece. */
function Figure({ text }: { text: string }) {
  const num = /^[+\-−]?[\d  .,]*\d/.exec(text)?.[0];
  if (!num || num.length === text.length) return <>{text}</>;
  return <>{num}<span className="fsa-unit">{text.slice(num.length).trim()}</span></>;
}

function Metrics({ block, facts }: { block: MetricsBlock; facts: Facts }) {
  const { d } = useEnv();
  const value = useValue();
  const shown = block.fact_ids.map((id) => facts.get(id)).filter((f): f is Fact => !!f);
  return (
    <section className="fsa-block fsa-metrics" aria-label={block.title ?? undefined}>
      {block.title && <h3 className="fsa-block-title">{block.title}</h3>}
      <ul>
        {shown.map((f) => (
          <li key={f.id} className="fsa-metric">
            <span className="fsa-metric-label">{label(d, f.label_key)}</span>
            <Origin fact={f} />
            <span className="fsa-metric-value"><Figure text={value(f)} /></span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Table({ block, facts }: { block: TableBlock; facts: Facts }) {
  const { d } = useEnv();
  const value = useValue();
  const cell = (id: string | null) => {
    const f = id ? facts.get(id) : undefined;
    return f ? value(f) : <span aria-label={d.evidence.noValue}>—</span>;
  };
  // The panel is narrow: three or more measures read better as one card per option than as squeezed columns.
  if (block.columns.length >= 3) {
    return (
      <section className="fsa-block fsa-rows" aria-label={block.title ?? undefined}>
        {block.title && <h3 className="fsa-block-title">{block.title}</h3>}
        <ul>
          {block.rows.map((r, i) => (
            <li key={i} className="fsa-row-card">
              <h4>{r.label}</h4>
              <dl>
                {block.columns.map((c, j) => (
                  <div key={j}>
                    <dt>{c}</dt>
                    <dd>{cell(r.fact_ids[j] ?? null)}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return (
    <section className="fsa-block fsa-table-wrap">
      <table className="fsa-table">
        {block.title && <caption className="fsa-block-title">{block.title}</caption>}
        <thead>
          <tr>
            <td />
            {block.columns.map((c, i) => <th key={i} scope="col">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((r, i) => (
            <tr key={i}>
              <th scope="row">{r.label}</th>
              {r.fact_ids.map((id, j) => {
                const f = id ? facts.get(id) : undefined;
                return <td key={j}>{f ? value(f) : <span aria-label={d.evidence.noValue}>—</span>}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function BarChart({ block, facts }: { block: BarChartBlock; facts: Facts }) {
  const value = useValue();
  const bars = block.bars.flatMap((b) => {
    const f = facts.get(b.fact_id);
    return f?.quantity ? [{ label: b.label, fact: f, v: f.quantity.value }] : [];
  });
  const max = Math.max(...bars.map((b) => Math.abs(b.v)), Number.MIN_VALUE);
  return (
    <section className="fsa-block fsa-bars">
      <h3 className="fsa-block-title">{block.title}</h3>
      <ul>
        {bars.map((b, i) => (
          <li key={i} className="fsa-bar">
            <span className="fsa-bar-label">{b.label}</span>
            <span className="fsa-bar-track" aria-hidden="true">
              <span className="fsa-bar-fill" style={{ width: `${Math.max(2, (Math.abs(b.v) / max) * 100)}%` }} />
            </span>
            <span className="fsa-bar-value">{value(b.fact)}</span>
          </li>
        ))}
      </ul>
      {bars[0] && <p className="fsa-bars-origin"><Origin fact={bars[0].fact} /></p>}
    </section>
  );
}

function Callout({ block, facts }: { block: CalloutBlock; facts: Facts }) {
  const { d } = useEnv();
  const value = useValue();
  const shown = (block.fact_ids ?? []).map((id) => facts.get(id)).filter((f): f is Fact => !!f);
  return (
    <div className={`fsa-block fsa-callout fsa-callout--${block.tone}`} role="note">
      {block.tone === "warning" ? <I.Alert className="fsa-ico" /> : <I.Info className="fsa-ico" />}
      <div>
        <p>{block.text}</p>
        {shown.length > 0 && (
          <ul className="fsa-callout-facts">
            {shown.map((f) => <li key={f.id}>{label(d, f.label_key)}: <b>{value(f)}</b></li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

function Body({ block, facts }: { block: MessageBlock; facts: Facts }) {
  switch (block.type) {
    case "metrics":
      return <Metrics block={block} facts={facts} />;
    case "table":
      return <Table block={block} facts={facts} />;
    case "bar_chart":
      return <BarChart block={block} facts={facts} />;
    case "callout":
      return <Callout block={block} facts={facts} />;
    default:
      return null;
  }
}

/** A presentation block, or null for text and questions (drawn by the message). Messages are checked against the
 *  contract first, so a block type this widget does not know never gets here: update agent and widget together.
 *  Rotation results carry the scenario the server verified; it is always said under them. */
export function PresentationBlock({ block, facts }: { block: MessageBlock; facts: Facts }) {
  const { d } = useEnv();
  const scenario = "scenario" in block ? block.scenario : null;
  const body = <Body block={block} facts={facts} />;
  if (!scenario) return body;
  const name = d.blocks.scenario[scenario] ?? scenario;
  return (
    <div className="fsa-block-group">
      {body}
      <p className="fsa-block-note">{fmt(d.blocks.simulation, { name })}</p>
    </div>
  );
}
