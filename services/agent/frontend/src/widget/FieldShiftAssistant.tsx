import { type CSSProperties, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CropOption } from "./actions/editing";
import type { HostCapabilities } from "./actions/policy";
import type { Citation, FieldContext } from "./contracts";
import { dictionaries, fmt, type Locale } from "./i18n/strings";
import { type ActionHandler, useAssistant } from "./state/useAssistant";
import type { TransportOptions } from "./transport/client";
import { Env, type WidgetEnv } from "./ui/context";
import { Launcher } from "./ui/Launcher";
import { Panel } from "./ui/Panel";
import { DEFAULT_TRUSTED_SOURCE_HOSTS } from "./ui/safeUrl";
import { useMedia } from "./ui/useMedia";
import "./styles/widget.css";

export const WIDGET_VERSION = "fieldshift-widget/0.4.0";

/**
 * CSS custom properties a host may override, e.g. `{ "--fsa-accent": "#1f5e4a" }`. Text on the accent is
 * --fsa-on-accent (light by default); for a light accent override both, e.g.
 * `{ "--fsa-accent": "#3fb08a", "--fsa-on-accent": "#03140d" }`.
 */
export type ThemeOverrides = Partial<Record<`--fsa-${string}`, string>>;

const NO_CROPS: readonly CropOption[] = [];

export interface FieldShiftAssistantProps {
  /** How to reach the agent service. Use a same-origin path behind your backend; never embed a server secret. */
  transport: TransportOptions;
  /** The host's current M0 FieldContext, or null when no field is selected. */
  fieldContext: FieldContext | null;
  /** Display name for the field (FieldContext has only an id). */
  fieldName?: string;
  /** The assistant's name in the header and the launcher's label. Default: "Польовий помічник" / "Field Assistant". */
  agentName?: string;
  locale: Locale;
  /** What the host can apply. The widget never offers more. */
  capabilities: HostCapabilities;
  /** Applies an action after the host's own checks (see `actionProblem`). */
  onAction: ActionHandler;
  /** Stable per browser session; generated if omitted. */
  sessionId?: string;
  /** Launcher corner and offsets, to avoid map controls. */
  placement?: { side?: "right" | "left"; offsetX?: number; offsetY?: number; zIndex?: number };
  theme?: ThemeOverrides;
  /** Hosts allowed in "View original source" links (https only). Default: FAO. */
  trustedSourceHosts?: readonly string[];
  /**
   * The crops this host app plans with (contract crop ids and their names in the current locale). Farmers can edit a
   * crop in a proposed condition only to one of these; without a list, crop proposals can be confirmed or rejected
   * as proposed but not edited. Other condition types can always be edited.
   */
  planningCrops?: readonly CropOption[];
  /** Show FieldContext.revision in the context bar (debugging). */
  showRevision?: boolean;
  /** Where to mount; default document.body, so map containers with transforms cannot clip it. */
  container?: Element | null;
  /** Open or close from the host's own button (e.g. an item of the app's navigation). Omit: the launcher does it. */
  open?: boolean;
  /** Called whenever the panel opens or closes, by the host or by the farmer (close button, Escape). */
  onOpenChange?: (open: boolean) => void;
  /** Open the host’s read-only saved conversations. Does not replace this widget’s thread. */
  onOpenHistory?: () => void;
  /** Show the round launcher. Default true; false when the host's own button opens the assistant. */
  launcher?: boolean;
  /** The host's button that opens the assistant: the panel unfolds from it and folds back into it. */
  anchor?: Element | null;
}

const MOBILE_QUERY = "(max-width: 640px)";
const CLOSE_MS = 200; // = the fold transition on .fsa-panel--closing in widget.css

export function FieldShiftAssistant(props: FieldShiftAssistantProps) {
  const { transport, fieldContext, fieldName, locale, capabilities, onAction, placement, theme, showRevision = false } = props;
  const d = dictionaries[locale];
  const name = props.agentName?.trim() || d.title;
  const [sessionId] = useState(() => props.sessionId ?? `ses_${crypto.randomUUID().replace(/-/g, "")}`);
  const assistant = useAssistant({ transport, fieldContext, locale, capabilities, onAction, sessionId, widgetVersion: WIDGET_VERSION });
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [citation, setCitation] = useState<{ key: string; citation: Citation; trigger: HTMLElement } | null>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const mobile = useMedia(MOBILE_QUERY);
  const reducedMotion = useMedia("(prefers-reduced-motion: reduce)");
  const panelId = `fsa-panel-${useId().replace(/:/g, "")}`;

  // Never opens by itself. The first open checks the service once; the farmer can re-check.
  const checked = useRef(false);
  useEffect(() => {
    if (open && !checked.current) {
      checked.current = true;
      void assistant.refreshBackend();
    }
  }, [open, assistant]);

  const foldTimer = useRef<number | undefined>(undefined);
  const close = useCallback(() => {
    if (foldTimer.current !== undefined) return; // already folding: one timer only
    assistant.cancel(); // closing the assistant stops the running turn
    setCitation(null);
    if (reducedMotion) {
      setOpen(false);
      return;
    }
    setClosing(true);
    foldTimer.current = window.setTimeout(() => {
      foldTimer.current = undefined;
      setClosing(false);
      setOpen(false);
    }, CLOSE_MS);
  }, [assistant, reducedMotion]);
  useEffect(() => () => window.clearTimeout(foldTimer.current), []);

  // Return focus to the launcher after closing (it is re-rendered on phones, hence an effect).
  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !open) launcher.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const toggle = useCallback(() => (open ? close() : setOpen(true)), [open, close]);

  // A host that opens the assistant from its own button: follow it, folding as the close button does.
  const wanted = props.open;
  useEffect(() => {
    if (wanted === undefined) return;
    if (wanted && !open) setOpen(true);
    else if (!wanted && open) close();
  }, [wanted]); // only a change of the host's wish, not every render
  const onOpenChange = props.onOpenChange;
  const reported = useRef(open);
  useEffect(() => {
    if (reported.current !== open) onOpenChange?.(open);
    reported.current = open;
  }, [open, onOpenChange]);

  // A citation belongs to one message of one field: close it when the field changes.
  useEffect(() => setCitation(null), [fieldContext?.field_id]);

  // Full-screen (modal) on phones: stop the page behind from scrolling while open.
  useEffect(() => {
    if (!(open && mobile)) return;
    const html = document.documentElement;
    const before = html.style.overflow;
    html.style.overflow = "hidden";
    return () => {
      html.style.overflow = before;
    };
  }, [open, mobile]);

  const openCitation = useCallback(
    (key: string, trigger: HTMLElement) => {
      const [itemId, citationId] = key.split(":");
      const item = assistant.thread.find((it) => it.kind === "assistant" && it.id === itemId);
      const c = item?.kind === "assistant" ? item.message?.citations?.find((x) => x.id === citationId) : undefined;
      if (c) setCitation({ key, citation: c, trigger });
    },
    [assistant.thread],
  );

  const closeCitation = useCallback(() => {
    const trigger = citation?.trigger;
    setCitation(null);
    requestAnimationFrame(() => (trigger?.isConnected ? trigger : launcher.current)?.focus());
  }, [citation]);

  const env: WidgetEnv = useMemo(
    () => ({
      d,
      locale,
      name,
      ctx: fieldContext,
      caps: capabilities,
      trustedSourceHosts: props.trustedSourceHosts ?? DEFAULT_TRUSTED_SOURCE_HOSTS,
      crops: props.planningCrops ?? NO_CROPS,
      assistant,
      openCitation,
    }),
    [d, locale, name, fieldContext, capabilities, props.trustedSourceHosts, props.planningCrops, assistant, openCitation],
  );

  const side = placement?.side ?? "right";
  const style = {
    ...theme,
    "--fsa-offset-x": `${placement?.offsetX ?? 24}px`,
    "--fsa-offset-y": `${placement?.offsetY ?? 24}px`,
    "--fsa-z": String(placement?.zIndex ?? 1000),
  } as CSSProperties;

  const ui = (
    <Env.Provider value={env}>
      <div className={`fsa-root fsa-root--${side}${props.launcher === false ? " fsa-root--no-launcher" : ""}`} style={style}
        lang={locale} data-reduced-motion={reducedMotion || undefined}>
        {open && (
          <Panel
            id={panelId}
            modal={mobile}
            closing={closing}
            fieldName={fieldName}
            showRevision={showRevision}
            citation={citation}
            onCloseCitation={closeCitation}
            onClose={close}
            onOpenHistory={props.onOpenHistory}
            anchor={props.anchor}
          />
        )}
        {props.launcher !== false && !(open && mobile) && (
          <Launcher
            ref={launcher}
            open={open}
            busy={assistant.busy}
            reducedMotion={reducedMotion}
            label={fmt(open ? d.launcherClose : d.launcherOpen, { name })}
            panelId={panelId}
            onToggle={toggle}
          />
        )}
      </div>
    </Env.Provider>
  );
  const container = props.container === undefined ? (typeof document !== "undefined" ? document.body : null) : props.container;
  return container ? createPortal(ui, container) : ui;
}
