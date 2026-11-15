import { type KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Citation } from "../contracts";
import { fmt, label } from "../i18n/strings";
import type { Item } from "../state/conversation";
import type { BackendStatus } from "../transport/client";
import { AssistantMessage } from "./AssistantMessage";
import { CitationDetail } from "./CitationDetail";
import { useEnv } from "./context";
import * as I from "./icons";
import { buildReport, downloadReport } from "./report";

const FOCUSABLE = 'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select,[tabindex]:not([tabindex="-1"]),summary';

function agentKind(backend: BackendStatus | null): "rules" | "scripted" | "llm" | null {
  if (!backend?.agent) return null;
  if (backend.agent.startsWith("deterministic")) return "rules";
  if (backend.agent.includes("scripted")) return "scripted";
  return "llm";
}

function Header({ onClose, titleId, onOpenHistory }: { onClose(): void; titleId: string; onOpenHistory?: () => void }) {
  const { d, name, assistant, locale } = useEnv();
  const { backend, checking, busy } = assistant;
  const state = busy ? "working" : checking && !backend ? "checking" : (backend?.state ?? "checking");
  const text = state === "not_ready" ? d.status.notReady : d.status[state as keyof typeof d.status];
  return (
    <header className="fsa-header">
      <span className={`fsa-mark fsa-header-mark${busy ? " fsa-mark--busy" : ""}`} aria-hidden="true">
        <I.FieldMark />
      </span>
      {/* the connection sits under the name, like a subtitle, so a long state never squeezes the name */}
      <div className="fsa-header-titles">
        <h2 id={titleId}>{name}</h2>
        <span className={`fsa-status fsa-status--${state}`}>
          <span className="fsa-status-dot" aria-hidden="true" />
          {text}
        </span>
      </div>
      {onOpenHistory && <button type="button" className="fsa-iconbtn" style={{ width: 44, height: 44 }}
        disabled={busy} onClick={onOpenHistory}
        aria-label={locale === "uk" ? "Збережені розмови" : "Saved conversations"}
        title={locale === "uk" ? "Збережені розмови" : "Saved conversations"}>
        <I.Book />
      </button>}
      <button type="button" className="fsa-iconbtn" onClick={onClose} aria-label={d.close}>
        <I.Close />
      </button>
    </header>
  );
}

function ContextBar({ fieldName, showRevision }: { fieldName?: string; showRevision: boolean }) {
  const { d, ctx, caps, assistant } = useEnv();
  const kind = agentKind(assistant.backend);
  const synthetic =
    !!ctx &&
    [...(ctx.facts ?? []).map((f) => f.provenance), ...(ctx.available_layers ?? []).map((l) => l.provenance)].some((p) => p.data_status === "fixture");
  const activeLayers = (ctx?.ui_state?.active_layer_ids ?? [])
    .map((id) => ctx?.available_layers?.find((l) => l.layer_id === id))
    .filter((l) => !!l)
    .map((l) => label(d, l.label_key));
  const simulation = assistant.backend?.simulation;
  const model = kind === "llm" && assistant.backend?.model ? ` (${assistant.backend.model})` : "";
  const hasMap = caps.actions.some((a) => a.startsWith("map.")); // no map on this page: "on the map" would mislead
  const showLayers = !!ctx && hasMap && activeLayers.length > 0;
  return (
    <div className="fsa-context">
      {ctx ? (
        <div className="fsa-context-row">
          <I.Pin className="fsa-ico" />
          <span className="fsa-context-field">
            <span className="fsa-sr">{d.context.field}: </span>
            {fieldName ?? ctx.field_id}
          </span>
          {showRevision && <span className="fsa-context-rev">{fmt(d.context.revision, { n: ctx.revision })}</span>}
          {synthetic && (
            <span className="fsa-badge fsa-badge--fixture" title={d.context.syntheticHint}>
              {d.context.synthetic}
              <span className="fsa-sr">: {d.context.syntheticHint}</span>
            </span>
          )}
        </div>
      ) : (
        <div className="fsa-context-row fsa-context-row--empty">
          <I.Pin className="fsa-ico" /> {d.context.noField}
        </div>
      )}
      {(kind || synthetic || showLayers || simulation) && (
        <p className="fsa-context-notes">
          {(kind || synthetic) && (
            <span>
              {synthetic && `${d.context.syntheticNote} `}
              {kind && (
                <span className="fsa-agent-kind" data-testid="agent-kind">
                  {fmt(d.agentKind[kind], { model })}
                </span>
              )}
            </span>
          )}
          {showLayers && (
            <span className="fsa-context-layers">
              {d.context.layers}: {activeLayers.join(", ")}
            </span>
          )}
          {simulation && <span className="fsa-agent-kind">{fmt(d.services.simulation, { mode: d.services.mode[simulation.mode] })}</span>}
        </p>
      )}
      {assistant.backend?.state === "offline" || assistant.backend?.state === "not_ready" ? (
        <div className="fsa-error" role="alert">
          <p>
            <I.Alert className="fsa-ico" /> {assistant.backend.state === "offline" ? d.errors.offline : d.errors.notReady}
          </p>
          <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" onClick={() => void assistant.refreshBackend()} disabled={assistant.checking}>
            {d.retry}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Welcome() {
  const { d, ctx, caps, assistant } = useEnv();
  const b = assistant.backend;
  const hasMoistureLayer = (ctx?.available_layers ?? []).some((l) => /moist|smap|волог/i.test(`${l.layer_id} ${l.label_key}`));
  const ready = b?.state === "ready";
  // Offer only what this backend and this host can actually do.
  // The scripted demo model only reads facts and searches FAO-56; rules and real models also act.
  const kind = agentKind(b);
  const acts = kind === "rules" || kind === "llm";
  // At most four (a short list is read; a long one is skipped), the most useful first.
  const keys = [
    "field",
    acts && hasMoistureLayer && caps.actions.includes("map.set_layers") && "moisture",
    acts && caps.actions.includes("compare.set_constraints") && "constraints",
    // Only when the host has a comparison view AND the agent reports a connected simulation service.
    (kind === "llm" || kind === "scripted") && caps.simulation && b?.simulation && "rotation",
    b?.knowledge && b.knowledge !== "unavailable" && "fao",
  ]
    .filter((k): k is keyof typeof d.suggestions => !!k)
    .slice(0, 4);
  return (
    <div className="fsa-welcome">
      <p className="fsa-welcome-text">{d.welcome}</p>
      {!ctx && <p className="fsa-note">{d.welcomeNoField}</p>}
      {ctx && (
        <div className="fsa-suggestions" role="group" aria-label={d.suggestionsLabel}>
          {keys.map((k) => (
            <button key={k} type="button" className="fsa-suggestion" disabled={!ready || assistant.busy} onClick={() => assistant.send(d.suggestions[k])}>
              {d.suggestions[k]}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function Conversation({ thread, inert, fieldName }: { thread: Item[]; inert?: boolean; fieldName?: string }) {
  const { d, locale, name, ctx, assistant } = useEnv();
  const answered = !assistant.busy && thread.some((it) => it.kind === "assistant" && it.status === "completed");
  const save = () => {
    const now = new Date();
    downloadReport(buildReport(thread, { d, locale, name, fieldName: fieldName ?? ctx?.field_id ?? "", now }), now);
  };
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // The scrollbar's own width, so the text keeps the same margin on both sides (0 for overlay scrollbars).
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.style.setProperty("--fsa-sbw", `${el.offsetWidth - el.clientWidth}px`);
  }, []);
  const last = thread[thread.length - 1];
  const tail = last?.kind === "assistant" ? `${last.status}:${last.draft.length}:${last.tools.length}:${Object.keys(last.actions).length}` : "";
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [thread.length, tail]);
  return (
    <div
      ref={scroller}
      className="fsa-scroll"
      inert={inert} // under an open source detail: no focus, no screen-reader access to the covered thread
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
      }}
    >
      {thread.length === 0 ? (
        <Welcome />
      ) : (
        <ol className="fsa-thread">
          {thread.map((it) =>
            it.kind === "user" ? (
              <li key={it.id} className="fsa-msg fsa-msg--user">
                <span className="fsa-sr">{d.you}: </span>
                <p>{it.text}</p>
              </li>
            ) : (
              <li key={it.id}>
                <AssistantMessage item={it} />
              </li>
            ),
          )}
        </ol>
      )}
      {answered && (
        <div className="fsa-report">
          <button type="button" className="fsa-btn fsa-btn--outline fsa-btn--sm" onClick={save}>
            <I.Book className="fsa-ico" />
            {d.report.save}
          </button>
        </div>
      )}
    </div>
  );
}

// The browser's own speech recognition (Web Speech API). Not in every browser; where it exists, Chrome and Edge
// recognise speech on their vendor's servers, which the microphone button's label says.
interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => Recognition;
const speechRecognition = (): RecognitionCtor | undefined => {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
};
const MAX_MESSAGE = 4000;

function Composer({ id, inputRef }: { id: string; inputRef: React.RefObject<HTMLTextAreaElement | null> }) {
  const { d, locale, ctx, assistant } = useEnv();
  const [text, setText] = useState("");
  const disabled = !ctx;
  const [Speech] = useState(speechRecognition);
  const dictation = useRef<Recognition | null>(null);
  const [listening, setListening] = useState(false);
  const [micNote, setMicNote] = useState<string | null>(null);
  const micButton = useRef<HTMLButtonElement>(null);
  useEffect(() => () => dictation.current?.abort(), []);
  useEffect(() => {
    if (disabled) dictation.current?.abort(); // the field went away: close the microphone
  }, [disabled]);

  const toggleDictation = () => {
    if (dictation.current) {
      dictation.current.stop(); // keeps what was heard; onend tidies up
      return;
    }
    if (!Speech || disabled) return;
    const r = new Speech();
    r.lang = locale === "uk" ? "uk-UA" : "en-US";
    r.interimResults = true;
    r.continuous = false;
    const before = text.trimEnd();
    r.onresult = (e) => {
      if (dictation.current !== r) return; // stopped or taken over by typing: late results are dropped
      let heard = "";
      for (let i = 0; i < e.results.length; i++) heard += e.results[i]![0]!.transcript;
      setText(`${before}${before ? " " : ""}${heard.trim()}`.slice(0, MAX_MESSAGE));
    };
    r.onerror = (e) => {
      if (e.error === "aborted") return;
      setMicNote(e.error === "not-allowed" || e.error === "service-not-allowed" ? d.mic.denied : d.mic.failed);
    };
    r.onend = () => {
      dictation.current = null;
      setListening(false);
      // back to the text only if the farmer is still on the microphone: recognition also ends by itself
      if (document.activeElement === micButton.current) inputRef.current?.focus();
    };
    dictation.current = r;
    setMicNote(null);
    setListening(true);
    try {
      r.start();
    } catch {
      dictation.current = null;
      setListening(false);
      setMicNote(d.mic.failed);
    }
  };

  const submit = () => {
    if (!text.trim() || assistant.busy || disabled) return;
    dictation.current?.abort();
    assistant.send(text);
    setText("");
  };
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
    el.style.overflowY = el.scrollHeight > 140 ? "auto" : "hidden";
  }, [text, inputRef]);
  const micLabel = Speech ? d.mic.start : d.mic.unsupported; // a toggle keeps its name; aria-pressed carries the state
  return (
    <>
    {/* always mounted, so screen readers announce the text when it appears */}
    <p className={micNote ? "fsa-note fsa-note--warn fsa-composer-note" : "fsa-sr"} role="status">{micNote ?? ""}</p>
    <form
      className="fsa-composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {/* Files are not part of the agent contract yet: the clip is shown, announced and labelled as unavailable. */}
      <button type="button" className="fsa-cbtn" aria-disabled="true" aria-label={d.attachUnavailable} title={d.attachUnavailable}>
        <I.Paperclip />
      </button>
      <label htmlFor={id} className="fsa-sr">{d.composerLabel}</label>
      <textarea
        id={id}
        ref={inputRef}
        rows={1}
        maxLength={MAX_MESSAGE}
        value={text}
        disabled={disabled}
        placeholder={disabled ? d.composerPlaceholderNoField : Speech ? d.composerPlaceholder : d.composerPlaceholderTyped}
        onChange={(e) => {
          dictation.current?.abort(); // typing takes over from dictation, so speech never overwrites it
          setText(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        enterKeyHint="send"
      />
      <button
        ref={micButton}
        type="button"
        className={`fsa-cbtn${listening ? " fsa-cbtn--listening" : ""}`}
        aria-label={micLabel}
        title={micLabel}
        aria-pressed={Speech ? listening : undefined}
        aria-disabled={!Speech || undefined}
        disabled={disabled}
        onClick={toggleDictation}
      >
        <I.Mic />
      </button>
      {assistant.busy ? (
        <button type="button" className="fsa-sendbtn fsa-sendbtn--stop" onClick={assistant.cancel} aria-label={d.stop}>
          <I.Stop />
        </button>
      ) : (
        <button type="submit" className="fsa-sendbtn" disabled={disabled || !text.trim()} aria-label={d.send}>
          <I.Send />
        </button>
      )}
    </form>
    </>
  );
}

export interface PanelProps {
  id: string;
  modal: boolean;
  closing: boolean;
  fieldName?: string;
  showRevision: boolean;
  citation: { key: string; citation: Citation } | null;
  onCloseCitation(): void;
  onClose(): void;
  onOpenHistory?: () => void;
  /** The host's button the panel unfolds from and folds back into (else the launcher's corner, in CSS). */
  anchor?: Element | null;
}

const UNFOLD_MS = 460; // = --fsa-t-unfold in widget.css

export function Panel({ id, modal, closing, fieldName, showRevision, citation, onCloseCitation, onClose, onOpenHistory, anchor }: PanelProps) {
  const { d, assistant } = useEnv();
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const titleId = `${id}-title`;
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    (input.current && !input.current.disabled ? input.current : root.current?.querySelector<HTMLElement>(".fsa-iconbtn"))?.focus();
  }, []);

  // Unfold from the host's button: the reveal circle is centred on it (it may lie outside the panel) and grows past
  // the far corner; closing folds back to it through the CSS transition, which reads the same properties.
  useLayoutEffect(() => {
    const panel = root.current;
    if (!panel || !anchor?.isConnected) return;
    const a = anchor.getBoundingClientRect();
    const p = panel.getBoundingClientRect();
    const x = a.left + a.width / 2 - p.left;
    const y = a.top + a.height / 2 - p.top;
    const far = Math.max(...[[0, 0], [p.width, 0], [0, p.height], [p.width, p.height]].map(([cx, cy]) => Math.hypot(cx! - x, cy! - y)));
    const fold = Math.min(a.width, a.height) / 2;
    panel.style.setProperty("--fsa-origin-x", `${x}px`);
    panel.style.setProperty("--fsa-origin-y", `${y}px`);
    panel.style.setProperty("--fsa-unfold-r", `${far + 24}px`);
    panel.style.setProperty("--fsa-fold-r", `${fold}px`);
    if (closing || typeof panel.animate !== "function" || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    panel.animate(
      [{ clipPath: `circle(${fold}px at ${x}px ${y}px)` }, { clipPath: `circle(${far + 24}px at ${x}px ${y}px)` }],
      { duration: UNFOLD_MS, easing: "cubic-bezier(0.2, 0, 0, 1)" },
    );
  }, [anchor, closing]);

  // One polite announcement per real state change: a tool starting, an answer arriving, a stop.
  const last = assistant.thread[assistant.thread.length - 1];
  const runningTool = last?.kind === "assistant" ? last.tools.filter((t) => t.status === "running").at(-1)?.tool : undefined;
  const lastStatus = last?.kind === "assistant" ? last.status : null;
  useEffect(() => {
    if (runningTool) setAnnouncement(d.activity[runningTool]);
  }, [runningTool, d]);
  useEffect(() => {
    if (lastStatus === "completed") setAnnouncement(d.liveRegion.answerReady);
    if (lastStatus === "failed") setAnnouncement(d.liveRegion.stopped);
  }, [lastStatus, d]);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (modal && e.key === "Tab" && root.current) {
      // Full-screen (mobile) mode is modal: keep Tab inside the panel.
      const items = [...root.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (!items.length) return;
      const first = items[0]!;
      const lastEl = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    <div
      ref={root}
      id={id}
      className={`fsa-panel${modal ? " fsa-panel--modal" : ""}${closing ? " fsa-panel--closing" : ""}`}
      role="dialog"
      aria-modal={modal ? true : undefined}
      aria-labelledby={titleId}
      onKeyDown={onKeyDown}
    >
      <Header onClose={onClose} titleId={titleId} onOpenHistory={onOpenHistory} />
      <ContextBar fieldName={fieldName} showRevision={showRevision} />
      <div className="fsa-main">
        <Conversation thread={assistant.thread} inert={!!citation} fieldName={fieldName} />
        {citation && (
          <div className="fsa-overlay">
            <CitationDetail key={citation.key} citation={citation.citation} onClose={onCloseCitation} />
          </div>
        )}
      </div>
      <Composer id={`${id}-input`} inputRef={input} />
      <div className="fsa-sr" aria-live="polite" aria-atomic="true">{announcement}</div>
    </div>
  );
}
