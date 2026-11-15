import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { Citation, Constraint, Fact, UIActionEnvelope } from "../contracts";
import { editBlock, unsupportedCrops } from "../actions/editing";
import { requiresClick } from "../actions/policy";
import { constraintText, formatQuantity } from "../i18n/format";
import { fmt, label } from "../i18n/strings";
import { proposedConstraints, type AssistantItem } from "../state/conversation";
import { errorTopic } from "../transport/errors";
import { ConstraintEditor } from "./ConstraintEditor";
import { useEnv } from "./context";
import * as I from "./icons";
import { PresentationBlock } from "./Blocks";
import { Markdown } from "./Markdown";

const SLOW_TOOL_MS = 15_000;

function Activity({ item }: { item: AssistantItem }) {
  const { d } = useEnv();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.max(0, Math.round((now - item.startedAt) / 1000));
  const slowSearch = item.tools.some((t) => t.status === "running" && t.tool === "search_knowledge") && now - item.startedAt > SLOW_TOOL_MS;
  return (
    <div className="fsa-activity" role="status" aria-live="off">
      <ul className="fsa-steps">
        {item.tools.map((t) => (
          <li key={t.callId} className={`fsa-step fsa-step--${t.status}`}>
            <span className="fsa-step-icon" aria-hidden="true">{t.status === "running" ? <span className="fsa-spinner" /> : t.status === "ok" ? <I.Check /> : <I.Alert />}</span>
            <span>{d.activity[t.tool]}</span>
            {t.status !== "running" && t.status !== "ok" && <span className="fsa-step-note">{d.activity[t.status]}</span>}
          </li>
        ))}
        {item.tools.every((t) => t.status !== "running") && !item.draft && (
          <li className="fsa-step fsa-step--running">
            <span className="fsa-step-icon" aria-hidden="true"><span className="fsa-spinner" /></span>
            <span>{d.activity.thinking}</span>
          </li>
        )}
      </ul>
      <span className="fsa-elapsed" aria-hidden="true">{fmt(d.activity.elapsed, { s: elapsed })}</span>
      {slowSearch && <p className="fsa-slow">{d.activity.slow}</p>}
    </div>
  );
}

function CitationRef({ itemId, citation }: { itemId: string; citation: Citation }) {
  const { d, openCitation } = useEnv();
  return (
    <button
      type="button"
      className="fsa-cite"
      aria-label={fmt(d.sources.cite, { n: citation.id, title: citation.title })}
      onClick={(e) => openCitation(`${itemId}:${citation.id}`, e.currentTarget)}
    >
      {citation.id}
    </button>
  );
}

function Sources({ item, citations }: { item: AssistantItem; citations: Citation[] }) {
  const { d, openCitation } = useEnv();
  return (
    <details className="fsa-sources">
      <summary>
        <I.Book className="fsa-ico" />
        <span>{fmt(d.sources.heading, { n: citations.length })}</span>
        <I.Chevron className="fsa-ico fsa-chev" />
      </summary>
      <ol>
        {citations.map((c) => (
          <li key={c.id}>
            <button type="button" className="fsa-source" onClick={(e) => openCitation(`${item.id}:${c.id}`, e.currentTarget)} aria-label={fmt(d.sources.open, { n: c.id, title: c.locator.table ?? c.locator.section ?? c.title })}>
              <span className="fsa-source-n" aria-hidden="true">{c.id}</span>
              <span className="fsa-source-body">
                <span className="fsa-source-title">{c.locator.table ?? c.locator.section ?? c.title}</span>
                <span className="fsa-source-meta">
                  {c.publisher} · {c.year}
                  {c.locator.chapter ? ` · ${fmt(d.sources.chapter, { v: c.locator.chapter })}` : ""}
                </span>
              </span>
              <I.Chevron className="fsa-ico" />
            </button>
          </li>
        ))}
      </ol>
      <p className="fsa-sources-note"><span className="fsa-badge fsa-badge--literature">{d.sources.literature}</span> {d.sources.disclaimer}</p>
    </details>
  );
}

function FactRow({ fact }: { fact: Fact }) {
  const { d, locale } = useEnv();
  const p = fact.provenance;
  // Provider and product exactly as the provenance names them (e.g. "NASA NSIDC DAAC · SPL4SMGP v008").
  const product = p.source
    ? `${p.source.provider} · ${p.source.product_id} ${fmt(d.evidence.version, { v: p.source.version })}`
    : p.model
      ? `${p.model.name} ${fmt(d.evidence.version, { v: p.model.version })}`
      : (p.method ?? null);
  const value = fact.quantity ? formatQuantity(fact.quantity, locale) : fact.category ?? d.evidence.noValue;
  return (
    <li className="fsa-fact">
      <div className="fsa-fact-main">
        <span className="fsa-fact-label">{label(d, fact.label_key)}</span>
        <span className="fsa-fact-value">{value}</span>
      </div>
      <div className="fsa-fact-meta">
        {fact.id.startsWith("sim.") && fact.domain === "rotation" && <span className="fsa-badge fsa-badge--simulation">{d.evidence.simulation}</span>}
        <span className={`fsa-badge fsa-badge--${p.kind}`}>{d.evidence.kind[p.kind]}</span>
        {p.data_status !== "live" && <span className={`fsa-badge fsa-badge--${p.data_status}`}>{d.evidence.status[p.data_status]}</span>}
        {p.kind === "ml_estimate" && !p.model?.validation && <span className="fsa-badge fsa-badge--warn">{d.evidence.notValidated}</span>}
        {product && <span className="fsa-fact-src">{product}</span>}
        {p.temporal && <span className="fsa-fact-src">{fmt(d.evidence.period, { start: p.temporal.start, end: p.temporal.end })}</span>}
      </div>
    </li>
  );
}

function Evidence({ facts }: { facts: Fact[] }) {
  const { d } = useEnv();
  return (
    <details className="fsa-evidence">
      <summary>
        <I.Satellite className="fsa-ico" />
        <span>{d.evidence.heading} ({facts.length})</span>
        <I.Chevron className="fsa-ico fsa-chev" />
      </summary>
      <ul>{facts.map((f) => <FactRow key={f.id} fact={f} />)}</ul>
    </details>
  );
}

function ConstraintGroup({ item, constraints }: { item: AssistantItem; constraints: Constraint[] }) {
  const { d, ctx, crops, assistant } = useEnv();
  const sameField = ctx?.field_id === item.fieldId;
  const cropLabels = useMemo(() => Object.fromEntries(crops.map((c) => [c.id, c.label])), [crops]);
  // The farmer's edits of proposals, applied but not decided (nothing is saved), and the editor that is open.
  // Both belong to the field view they were made in: another field or revision discards them.
  const [edits, setEdits] = useState<Record<string, Constraint>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const editButtons = useRef(new Map<string, HTMLButtonElement | null>());
  const view = `${ctx?.field_id ?? ""}#${ctx?.revision ?? ""}`;
  const [editedFor, setEditedFor] = useState(view);
  if (editedFor !== view) {
    setEditedFor(view);
    setEdits({});
    setEditing(null);
  }
  const closeEditor = (id: string) => {
    setEditing(null);
    queueMicrotask(() => editButtons.current.get(id)?.focus());
  };

  // The host's live context is the record of the farmer's decisions: a constraint shows as confirmed or rejected
  // only once the host holds it so, never on the widget's word alone. The decided value is the one sent: the
  // farmer's edit, if any.
  const inContext = (c: Constraint, status: "confirmed" | "rejected" = "confirmed") =>
    sameField && (ctx?.constraints ?? []).some((k) => k.status === status && k.type === c.type && JSON.stringify(k.value) === JSON.stringify(c.value));
  const decidedValue = (c: Constraint): Constraint => {
    const state = item.constraints[c.id];
    return (state && "constraint" in state && state.constraint) || edits[c.id] || c;
  };
  // A decision this conversation made (or is making) for the constraint, as opposed to one the host already held.
  const ownDecision = (c: Constraint) => {
    const phase = item.constraints[c.id]?.phase;
    return phase === "applying" || phase === "confirmed" || phase === "declined";
  };
  const phaseOf = (c: Constraint) => {
    const v = decidedValue(c);
    if (!ownDecision(c)) {
      // The host already holds this exact decision, saved before: say so, never as if it had just been saved.
      if (inContext(v)) return "heldConfirmed";
      if (inContext(v, "rejected")) return "heldRejected";
    }
    return inContext(v) ? "confirmed" : inContext(v, "rejected") ? "declined" : (item.constraints[c.id]?.phase ?? "proposed");
  };
  const pending = constraints.filter((c) => phaseOf(c) === "proposed");
  return (
    <section className="fsa-group" aria-label={d.constraints.heading}>
      <h3 className="fsa-group-title">{d.constraints.heading}</h3>
      <ul className="fsa-constraints">
        {constraints.map((c) => {
          const current = decidedValue(c);
          const edited = current !== c;
          const text = constraintText(d, current, cropLabels);
          const phase = phaseOf(c);
          const state = item.constraints[c.id];
          // Shown as decided only when the host's live FieldContext holds that decision.
          const shown =
            (phase === "confirmed" && !inContext(current)) || (phase === "declined" && !inContext(current, "rejected")) ? "applying" : phase;
          const open = editing === c.id && shown === "proposed" && sameField;
          const block = editBlock(c, crops);
          const held = shown === "heldConfirmed" || shown === "heldRejected";
          const look = shown === "heldConfirmed" ? "confirmed" : shown === "heldRejected" ? "declined" : shown;
          return (
            <li key={c.id} className={`fsa-constraint fsa-constraint--${look}${edited ? " fsa-constraint--edited" : ""}`}>
              <div className="fsa-constraint-main">
                <span className="fsa-constraint-icon" aria-hidden="true">{c.type === "irrigation_available" ? <I.Drop /> : <I.Sprout />}</span>
                <span className="fsa-constraint-text">{text}</span>
                <span className="fsa-state">
                  {shown === "proposed" && (edited ? d.constraints.edited : d.constraints.proposed)}
                  {shown === "applying" && d.constraints.applying}
                  {shown === "confirmed" && <><I.Check className="fsa-ico" /> {d.constraints.confirmed}</>}
                  {shown === "declined" && d.constraints.declined}
                  {shown === "heldConfirmed" && <><I.Check className="fsa-ico" /> {d.constraints.heldConfirmed}</>}
                  {shown === "heldRejected" && d.constraints.heldRejected}
                  {shown === "dismissed" && d.constraints.dismissed}
                </span>
              </div>
              <p className="fsa-constraint-why">
                {edited
                  ? <>{d.constraints.chosenInApp}. {fmt(d.constraints.proposedWas, { v: constraintText(d, c, cropLabels) })}</>
                  : c.source === "agent_inferred" ? d.constraints.inferred : c.evidence ? fmt(d.constraints.evidence, { e: c.evidence }) : null}
              </p>
              {state?.phase === "rejected" && (
                <p className="fsa-note fsa-note--warn" role="status">
                  {state.reason === "field_changed" ? d.constraints.stale : fmt(d.constraints.rejected, { reason: d.actions.rejected[state.reason] })}
                </p>
              )}
              {shown === "proposed" && sameField && block === "unshared_crops" && (
                <p className="fsa-note">
                  {fmt(d.constraints.cannotEditCrops, { v: unsupportedCrops(c, crops).map((id) => d.crops[id] ?? id.replace(/_/g, " ")).join(", ") })}
                </p>
              )}
              {held && edited && sameField && (
                <div className="fsa-row">
                  <button type="button" className="fsa-btn fsa-btn--ghost fsa-btn--sm" aria-label={fmt(d.constraints.restoreLabel, { c: constraintText(d, c, cropLabels) })}
                    onClick={() => setEdits((all) => { const rest = { ...all }; delete rest[c.id]; return rest; })}>
                    {d.constraints.restore}
                  </button>
                </div>
              )}
              {open && (
                <ConstraintEditor
                  constraint={current}
                  proposal={c}
                  onApply={(next) => {
                    setEdits((all) => {
                      const rest = { ...all };
                      delete rest[c.id];
                      return next === c ? rest : { ...rest, [c.id]: next };
                    });
                    closeEditor(c.id);
                  }}
                  onCancel={() => closeEditor(c.id)}
                />
              )}
              {shown === "proposed" && sameField && !open && (
                <div className="fsa-row">
                  <button type="button" className="fsa-btn fsa-btn--primary fsa-btn--sm" aria-label={fmt(d.constraints.confirmLabel, { c: text })} onClick={() => void assistant.confirmConstraints(item.id, [current], text)}>
                    <I.Check className="fsa-ico" /> {d.constraints.confirm}
                  </button>
                  <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" aria-label={fmt(d.constraints.rejectLabel, { c: text })} onClick={() => void assistant.rejectConstraints(item.id, [current], text)}>
                    {d.constraints.reject}
                  </button>
                  {block === null && (
                    <button type="button" className="fsa-btn fsa-btn--ghost fsa-btn--sm" aria-label={fmt(d.constraints.editLabel, { c: text })}
                      ref={(el) => { editButtons.current.set(c.id, el); }} onClick={() => setEditing(c.id)}>
                      {d.constraints.edit}
                    </button>
                  )}
                  {edited && (
                    <button type="button" className="fsa-btn fsa-btn--ghost fsa-btn--sm" aria-label={fmt(d.constraints.restoreLabel, { c: constraintText(d, c, cropLabels) })}
                      onClick={() => setEdits((all) => { const rest = { ...all }; delete rest[c.id]; return rest; })}>
                      {d.constraints.restore}
                    </button>
                  )}
                  <button type="button" className="fsa-btn fsa-btn--ghost fsa-btn--sm" aria-label={fmt(d.constraints.dismissLabel, { c: text })} title={d.constraints.dismissHint} onClick={() => assistant.dismissConstraint(item.id, c.id)}>
                    {d.constraints.dismiss}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {pending.length > 1 && sameField && editing === null && (
        <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" onClick={() => void assistant.confirmConstraints(item.id, pending.map(decidedValue), pending.map((c) => constraintText(d, decidedValue(c), cropLabels)).join(", "))}>
          {d.constraints.confirmAll}
        </button>
      )}
    </section>
  );
}

function actionIcon(env: UIActionEnvelope) {
  switch (env.action.type) {
    case "map.set_layers":
      return <I.Layers />;
    case "chart.focus":
    case "time.set_range":
      return <I.Chart />;
    case "map.focus_field":
      return <I.Pin />;
    case "scenario.set":
      return <I.Scenario />;
    default: // opening a screen or a panel; the row's own round arrow says "go"
      return <I.Screen />;
  }
}

function ActionGroup({ item, actions }: { item: AssistantItem; actions: UIActionEnvelope[] }) {
  const { d, caps, assistant } = useEnv();
  return (
    <section className="fsa-group" aria-label={d.actions.heading}>
      <h3 className="fsa-group-title">{d.actions.heading}</h3>
      <ul className="fsa-actions">
        {actions.map((env) => {
          const st = item.actions[env.id] ?? { phase: "idle" as const };
          const unavailable = !caps.actions.includes(env.action.type);
          const note =
            st.phase === "rejected" ? d.actions.rejected[st.reason]
            : st.phase === "applied" && env.action.type === "scenario.set" && !caps.simulation ? d.actions.scenarioOnly
            : unavailable && st.phase === "idle" ? d.actions.rejected.module_unavailable
            : null;
          return (
            <li key={env.id} className={`fsa-action fsa-action--${st.phase}`}>
              {requiresClick(env) && st.phase !== "applied" ? (
                <button
                  type="button"
                  className="fsa-chip"
                  disabled={unavailable || st.phase === "applying"}
                  aria-describedby={env.reason ? `${item.id}-${env.id}-why` : undefined}
                  onClick={() => assistant.applyAction(item.id, env)}
                >
                  <span className="fsa-chip-icon" aria-hidden="true">{actionIcon(env)}</span>
                  <span>{env.label}</span>
                  {st.phase === "applying" && <span className="fsa-spinner" aria-hidden="true" />}
                </button>
              ) : (
                <p className="fsa-action-status">
                  <span className="fsa-chip-icon" aria-hidden="true">{st.phase === "applied" ? <I.Check /> : actionIcon(env)}</span>
                  <span>{env.label}</span>
                  <span className="fsa-state">
                    {st.phase === "applied" ? (st.auto ? d.actions.appliedAuto : d.actions.applied) : st.phase === "applying" ? d.actions.applying : null}
                  </span>
                </p>
              )}
              {env.reason && <p id={`${item.id}-${env.id}-why`} className="fsa-action-why">{env.reason}</p>}
              {note && <p className={`fsa-note ${st.phase === "rejected" || unavailable ? "fsa-note--warn" : ""}`} role="status">{note}</p>}
            </li>
          );
        })}
      </ul>
      {item.back && (
        <div className="fsa-back">
          {item.back.phase === "done" ? (
            <p className="fsa-note" role="status">{d.actions.backDone}</p>
          ) : item.back.phase === "failed" ? (
            <p className="fsa-note fsa-note--warn" role="status">{d.actions.backFailed}</p>
          ) : (
            <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" disabled={item.back.phase === "applying"}
              onClick={() => void assistant.goBack(item.id, d.actions.back)}>
              <I.Undo className="fsa-ico" />
              {d.actions.back}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function Failure({ item }: { item: AssistantItem }) {
  const { d, assistant } = useEnv();
  const e = item.error!;
  const topic = errorTopic(e);
  const quiet = topic === "cancelled" || topic === "fieldChanged";
  return (
    <div className={`fsa-error ${quiet ? "fsa-error--quiet" : ""}`} role={quiet ? "status" : "alert"}>
      <p>
        {!quiet && <I.Alert className="fsa-ico" />} {d.errors[topic]}
        {item.discardedDraft && ` ${d.partialDiscarded}`}
      </p>
      {e.retryable && (
        <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" onClick={() => assistant.retry(item.id)}>
          {d.retry}
        </button>
      )}
    </div>
  );
}

function AssistantMessageView({ item }: { item: AssistantItem }) {
  const { d, assistant } = useEnv();
  const m = item.message;
  const citations = m?.citations ?? [];
  const byId = new Map(citations.map((c) => [c.id, c]));
  const factsById = new Map((m?.facts ?? []).map((f) => [f.id, f]));
  const cite = (id: string, key: string) => {
    const c = byId.get(id);
    return c ? <CitationRef key={key} itemId={item.id} citation={c} /> : null;
  };
  const constraints = m ? proposedConstraints(m) : [];
  // `compare.set_constraints` proposals are shown as constraint chips, not as separate actions.
  const actions = (m?.ui_actions ?? []).filter((a) => a.action.type !== "compare.set_constraints");
  return (
    <article className="fsa-msg fsa-msg--assistant" aria-label={d.assistant} aria-busy={item.status === "streaming"}>
      <div className="fsa-msg-body">
        {item.status === "streaming" && <Activity item={item} />}
        {item.status === "streaming" && item.draft && (
          <div className="fsa-md fsa-md--draft"><Markdown text={item.draft} cite={() => null} /></div>
        )}
        {m &&
          m.blocks.map((b, i) =>
            b.type === "text" ? (
              <div key={i} className="fsa-md"><Markdown text={b.markdown} cite={cite} /></div>
            ) : b.type !== "clarifying_question" ? (
              <PresentationBlock key={i} block={b} facts={factsById} />
            ) : (
              <div key={i} className="fsa-clarify">
                <p>{b.text}</p>
                {!!b.options?.length && (
                  <div className="fsa-row" role="group" aria-label={d.clarify}>
                    {b.options.map((o) => (
                      <button key={o} type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" disabled={assistant.busy} onClick={() => assistant.send(o)}>
                        {o}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ),
          )}
        {m && !!m.warnings?.length && (
          <ul className="fsa-warnings">
            {m.warnings.map((w, i) => (
              <li key={i} className={`fsa-warning fsa-warning--${w.code}`}>
                {w.code === "fixture_data" ? <I.Info className="fsa-ico" /> : <I.Alert className="fsa-ico" />}
                <span>{d.warnings[w.code]}</span>
              </li>
            ))}
          </ul>
        )}
        {m && constraints.length > 0 && <ConstraintGroup item={item} constraints={constraints} />}
        {m && actions.length > 0 && <ActionGroup item={item} actions={actions} />}
        {m && !!m.facts?.length && <Evidence facts={m.facts} />}
        {m && citations.length > 0 && <Sources item={item} citations={citations} />}
        {item.status === "failed" && (
          <>
            {item.error && <Failure item={item} />}
          </>
        )}
      </div>
    </article>
  );
}

export const AssistantMessage = memo(AssistantMessageView);
