import { useEffect, useRef } from "react";
import type { Citation } from "../contracts";
import { fmt } from "../i18n/strings";
import { useEnv } from "./context";
import * as I from "./icons";
import { safeSourceUrl } from "./safeUrl";

/**
 * One verified source, shown over the conversation inside the panel. Everything here comes from
 * the server's Citation object (corpus metadata); nothing is taken from the answer's prose.
 */
export function CitationDetail({ citation, onClose }: { citation: Citation; onClose(): void }) {
  const { d, trustedSourceHosts } = useEnv();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const href = safeSourceUrl(citation.url, trustedSourceHosts);
  const loc = citation.locator;
  return (
    <section
      className="fsa-cdetail"
      aria-labelledby="fsa-cdetail-title"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation(); // close the source, not the whole assistant
          onClose();
        }
      }}
    >
      <div className="fsa-cdetail-bar">
        <button type="button" className="fsa-iconbtn" onClick={onClose} aria-label={d.sources.back}>
          <I.Back />
        </button>
        <h3 id="fsa-cdetail-title" ref={heading} tabIndex={-1}>
          {fmt(d.sources.detailTitle, { n: citation.id })}
        </h3>
      </div>
      <div className="fsa-cdetail-body">
        <span className="fsa-badge fsa-badge--literature">{d.sources.literature}</span>
        <p className="fsa-cdetail-title" lang={citation.language}>{citation.title}</p>
        <p className="fsa-cdetail-pub">
          {fmt(d.sources.publisher, { publisher: citation.publisher, year: citation.year })}
          {citation.edition ? ` · ${citation.edition}` : ""}
        </p>
        <dl className="fsa-locator">
          {loc.chapter && (
            <div>
              <dt className="fsa-sr">{fmt(d.sources.chapter, { v: "" })}</dt>
              <dd>{fmt(d.sources.chapter, { v: loc.chapter })}</dd>
            </div>
          )}
          {loc.section && (
            <div>
              <dt>{d.sources.section}</dt>
              <dd>{loc.section}</dd>
            </div>
          )}
          {loc.table && (
            <div>
              <dt>{d.sources.table}</dt>
              <dd>{loc.table.replace(/^Table\s*/i, "")}</dd>
            </div>
          )}
          {loc.page != null && (
            <div>
              <dd>{fmt(d.sources.page, { v: loc.page })}</dd>
            </div>
          )}
        </dl>
        <figure className="fsa-quote">
          <figcaption>
            {d.sources.excerpt} · {fmt(d.sources.originalLanguage, { lang: d.langName[citation.language] })}
          </figcaption>
          <blockquote lang={citation.language}>{citation.quote}</blockquote>
        </figure>
        {href ? (
          <a className="fsa-btn fsa-btn--outline fsa-source-link" href={href} target="_blank" rel="noopener noreferrer external" referrerPolicy="no-referrer">
            {d.sources.viewOriginal} <I.Arrow className="fsa-ico" />
          </a>
        ) : (
          <p className="fsa-note fsa-note--warn">{d.sources.linkBlocked}</p>
        )}
        <div className="fsa-licence">
          <h4>{d.sources.licence}</h4>
          <p>{citation.licence}</p>
        </div>
        <p className="fsa-note">{d.sources.disclaimer}</p>
      </div>
    </section>
  );
}
