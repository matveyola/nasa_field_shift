import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { AgentMessage, AgentRequest, Constraint, FieldContext, UIActionEnvelope } from "../contracts";
import {
  type ActionRequest,
  type ActionResult,
  backAction,
  confirmationEnvelope,
  type HostCapabilities,
  type RejectReason,
  requiresClick,
} from "../actions/policy";
import type { Locale } from "../i18n/strings";
import { type BackendStatus, checkBackend, streamTurn, type TransportOptions } from "../transport/client";
import { type AbortReason, AgentError } from "../transport/errors";
import { type AssistantItem, buildHistory, initialState, type Item, reducer } from "./conversation";

export type ActionHandler = (request: ActionRequest) => Promise<ActionResult> | ActionResult;

export interface AssistantConfig {
  transport: TransportOptions;
  fieldContext: FieldContext | null;
  locale: Locale;
  capabilities: HostCapabilities;
  onAction: ActionHandler;
  sessionId: string;
  widgetVersion: string;
}

const MAX_MESSAGE_CHARS = 4000; // UserMessage.text maxLength

function newId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
}

interface ActiveTurn {
  itemId: string;
  fieldId: string;
  controller: AbortController;
}

/**
 * Conversation and turn lifecycle. One turn at a time. A turn ends exactly once (completed,
 * failed or aborted); its callbacks are ignored once it is no longer the active turn, so a slow
 * answer for field A can never change field B's conversation or apply actions to field B.
 */
export function useAssistant(config: AssistantConfig) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [backend, setBackend] = useState<BackendStatus | null>(null);
  const [checking, setChecking] = useState(false);

  // Latest values for async callbacks (they must see the host's current context, not a stale closure).
  const live = useRef(config);
  live.current = config;
  const stateRef = useRef(state);
  stateRef.current = state;
  const active = useRef<ActiveTurn | null>(null);
  const abortReasons = useRef(new Map<string, AbortReason>());
  // Revisions the host reported after applying an action. The host's store moves on before React
  // re-renders this component with the new FieldContext, so these refs are the freshest truth.
  const hostRevisions = useRef(new Map<string, number[]>()); // per assistant item
  const lastHost = useRef<{ fieldId: string; revision: number } | null>(null);

  const liveRevision = (ctx: FieldContext): number =>
    lastHost.current?.fieldId === ctx.field_id ? Math.max(ctx.revision, lastHost.current.revision) : ctx.revision;

  const stillCurrent = (item: AssistantItem, ctx: FieldContext | null): boolean => {
    if (!ctx || ctx.field_id !== item.fieldId) return false;
    const rev = liveRevision(ctx);
    return item.validRevisions.includes(rev) || (hostRevisions.current.get(item.id) ?? []).includes(rev);
  };

  const recordRevision = (fid: string, itemId: string, revision: number | undefined) => {
    if (revision == null) return;
    hostRevisions.current.set(itemId, [...(hostRevisions.current.get(itemId) ?? []), revision]);
    lastHost.current = { fieldId: fid, revision };
    dispatch({ type: "revision", fieldId: fid, itemId, revision });
  };

  const fieldId = config.fieldContext?.field_id ?? null;
  const thread: Item[] = useMemo(() => (fieldId ? (state.threads[fieldId] ?? []) : []), [state.threads, fieldId]);
  const busy = thread.some((it) => it.kind === "assistant" && it.status === "streaming");

  const findItem = (fid: string, itemId: string): AssistantItem | undefined =>
    stateRef.current.threads[fid]?.find((it): it is AssistantItem => it.kind === "assistant" && it.id === itemId);

  const abort = useCallback((reason: AbortReason) => {
    const turn = active.current;
    if (!turn) return;
    active.current = null; // from here on, this turn's late results are ignored
    abortReasons.current.set(turn.itemId, reason);
    turn.controller.abort(reason);
  }, []);

  const refreshBackend = useCallback(async () => {
    setChecking(true);
    try {
      setBackend(await checkBackend(live.current.transport));
    } finally {
      setChecking(false);
    }
  }, []);

  // ------------------------------------------------------------------ host actions

  const reject = (fid: string, itemId: string, actionId: string, reason: RejectReason) =>
    dispatch({ type: "actionPhase", fieldId: fid, itemId, actionId, phase: { phase: "rejected", reason } });

  /** Re-checks an action against the live context, hands it to the host, and records the outcome. */
  const runAction = useCallback(
    async (fid: string, itemId: string, envelope: UIActionEnvelope, origin: ActionRequest["origin"]) => {
      const item = findItem(fid, itemId);
      const ctx = live.current.fieldContext;
      if (!item) return;
      if (!stillCurrent(item, ctx)) {
        return reject(fid, itemId, envelope.id, ctx?.field_id === item.fieldId ? "stale_context" : "field_changed");
      }
      if (!live.current.capabilities.actions.includes(envelope.action.type)) {
        return reject(fid, itemId, envelope.id, "module_unavailable");
      }
      // The widget vouches for the revision chain; the host still re-checks ids against its store.
      let checked: UIActionEnvelope = { ...envelope, field_context_revision: liveRevision(ctx!) };
      if (checked.action.type === "compare.set_constraints" && origin === "farmer_click") {
        checked = { ...checked, action: { ...checked.action, constraints: checked.action.constraints.map((c) => ({ ...c, status: "confirmed" })) } };
      }
      dispatch({ type: "actionPhase", fieldId: fid, itemId, actionId: envelope.id, phase: { phase: "applying" } });
      let result: ActionResult;
      try {
        result = await live.current.onAction({ envelope: checked, fieldId: item.fieldId, origin });
      } catch {
        result = { status: "rejected", reason: "failed" };
      }
      if (result.status === "applied") {
        recordRevision(fid, itemId, result.revision);
        dispatch({ type: "actionPhase", fieldId: fid, itemId, actionId: envelope.id, phase: { phase: "applied", auto: origin === "agent_auto" } });
      } else {
        reject(fid, itemId, envelope.id, result.reason);
      }
    },
    [],
  );

  const applyAutoActions = useCallback(
    async (fid: string, itemId: string, message: AgentMessage) => {
      const autos = (message.ui_actions ?? []).filter((e) => !requiresClick(e));
      const before = live.current.fieldContext?.ui_state;
      if (before && autos.some((e) => backAction(e.action.type, before))) {
        dispatch({ type: "back", fieldId: fid, itemId, back: { before, phase: "ready" } });
      }
      for (const envelope of autos) await runAction(fid, itemId, envelope, "agent_auto"); // sequential: revisions chain
    },
    [runAction],
  );

  /** The farmer pressed "Back": undo this answer's automatic view changes, while nothing else has changed since. */
  const goBack = useCallback(async (itemId: string, label: string) => {
    const ctx = live.current.fieldContext;
    const fid = ctx?.field_id;
    const item = fid ? findItem(fid, itemId) : undefined;
    if (!fid || !item?.back || item.back.phase !== "ready") return;
    const { before } = item.back;
    const set = (phase: "applying" | "done" | "failed") => dispatch({ type: "back", fieldId: fid, itemId, back: { before, phase } });
    if (!stillCurrent(item, ctx)) return set("failed");
    const undo = (item.message?.ui_actions ?? [])
      .filter((e) => { const st = item.actions[e.id]; return st?.phase === "applied" && st.auto; })
      .map((e) => [e.id, backAction(e.action.type, before)] as const)
      .filter((x): x is readonly [string, NonNullable<ReturnType<typeof backAction>>] => !!x[1]);
    set("applying");
    for (const [id, action] of undo) {
      const now = live.current.fieldContext;
      if (!now || !live.current.capabilities.actions.includes(action.type)) return set("failed");
      const envelope: UIActionEnvelope = { id: `${id}.back`, field_context_revision: liveRevision(now), trigger: "chip", label, action };
      let result: ActionResult;
      try {
        result = await live.current.onAction({ envelope, fieldId: fid, origin: "farmer_click" });
      } catch {
        result = { status: "rejected", reason: "failed" };
      }
      if (result.status !== "applied") return set("failed");
      recordRevision(fid, itemId, result.revision);
    }
    set("done");
  }, []);

  const applyAction = useCallback(
    (itemId: string, envelope: UIActionEnvelope) => {
      const fid = live.current.fieldContext?.field_id;
      if (fid) void runAction(fid, itemId, envelope, "farmer_click");
    },
    [runAction],
  );

  /** The farmer confirmed one or more proposed constraints. Only now does anything reach the host. */
  const confirmConstraints = useCallback(async (itemId: string, constraints: Constraint[], label: string) => {
    const ctx = live.current.fieldContext;
    const fid = ctx?.field_id;
    const item = fid ? findItem(fid, itemId) : undefined;
    if (!ctx || !fid || !item || constraints.length === 0) return;
    const ids = constraints.map((c) => c.id);
    const set = (phase: AssistantItem["constraints"][string]) => dispatch({ type: "constraintPhase", fieldId: fid, itemId, constraintIds: ids, phase });
    if (item.fieldId !== ctx.field_id) return set({ phase: "rejected", reason: "field_changed" });
    if (!live.current.capabilities.actions.includes("compare.set_constraints")) return set({ phase: "rejected", reason: "module_unavailable" });
    // A confirmation is the farmer's own decision, made now, for this field: it carries the current revision.
    const base = confirmationEnvelope(constraints[0]!, { ...ctx, revision: liveRevision(ctx) }, label);
    const envelope: UIActionEnvelope =
      constraints.length === 1
        ? base
        : { ...base, id: `confirm.${itemId}`, action: { type: "compare.set_constraints", constraints: constraints.map((c) => ({ ...c, status: "confirmed" })) } };
    set({ phase: "applying" });
    let result: ActionResult;
    try {
      result = await live.current.onAction({ envelope, fieldId: fid, origin: "farmer_click" });
    } catch {
      result = { status: "rejected", reason: "failed" };
    }
    if (result.status === "applied") {
      recordRevision(fid, itemId, result.revision);
      set({ phase: "confirmed" });
    } else {
      set({ phase: "rejected", reason: result.reason });
    }
  }, []);

  const dismissConstraint = useCallback((itemId: string, constraintId: string) => {
    const fid = live.current.fieldContext?.field_id;
    if (fid) dispatch({ type: "constraintPhase", fieldId: fid, itemId, constraintIds: [constraintId], phase: { phase: "dismissed" } });
  }, []);

  // ------------------------------------------------------------------ turns

  const send = useCallback(
    (raw: string) => {
      const cfg = live.current;
      const ctx = cfg.fieldContext;
      const text = raw.trim().slice(0, MAX_MESSAGE_CHARS);
      if (!ctx || !text || active.current) return;
      const fid = ctx.field_id;
      const request: AgentRequest = {
        request_id: newId("req"),
        session_id: cfg.sessionId,
        locale: cfg.locale,
        message: { text },
        history: buildHistory(stateRef.current.threads[fid] ?? []),
        field_context: ctx,
        client: {
          widget_version: cfg.widgetVersion,
          // what the app can show, so the agent opens only that and otherwise says where to go
          capabilities: { actions: [...cfg.capabilities.actions], screens: [...(cfg.capabilities.screens ?? [])] },
        },
      };
      const itemId = newId("turn");
      dispatch({
        type: "start",
        fieldId: fid,
        user: { kind: "user", id: newId("msg"), text },
        assistant: {
          kind: "assistant", id: itemId, question: text, fieldId: fid, validRevisions: [ctx.revision], status: "streaming",
          startedAt: Date.now(), tools: [], draft: "", discardedDraft: false, message: null, error: null, actions: {}, constraints: {},
        },
      });
      const controller = new AbortController();
      active.current = { itemId, fieldId: fid, controller };
      const isActive = () => active.current?.itemId === itemId;

      streamTurn(cfg.transport, request, { onEvent: (event) => isActive() && dispatch({ type: "event", fieldId: fid, itemId, event }) }, controller.signal)
        .then((message) => {
          if (!isActive()) {
            // Finished after it was cancelled (field switch, stop): the answer is never shown or applied.
            const abortReason = abortReasons.current.get(itemId) ?? "user";
            dispatch({ type: "fail", fieldId: fid, itemId, error: new AgentError("aborted", false, { abortReason }) });
            return;
          }
          active.current = null;
          dispatch({ type: "complete", fieldId: fid, itemId, message });
          // Let the reducer commit before auto actions read the item.
          queueMicrotask(() => void applyAutoActions(fid, itemId, message));
        })
        .catch((err: unknown) => {
          if (isActive()) active.current = null;
          const error = err instanceof AgentError ? err : new AgentError("protocol", false, { detail: String(err) });
          dispatch({ type: "fail", fieldId: fid, itemId, error });
          if (error.kind === "network" || (error.kind === "http" && (error.info.status ?? 0) >= 500)) void refreshBackend();
        });
    },
    [applyAutoActions, refreshBackend],
  );

  /** "Try again" after a failure: the farmer's click is the authorisation to resend. */
  const retry = useCallback(
    (itemId: string) => {
      const fid = live.current.fieldContext?.field_id;
      const list = fid ? stateRef.current.threads[fid] : undefined;
      const idx = list?.findIndex((it) => it.id === itemId) ?? -1;
      const item = list?.[idx];
      const user = list?.[idx - 1];
      if (!fid || item?.kind !== "assistant" || item.status !== "failed" || user?.kind !== "user") return;
      dispatch({ type: "remove", fieldId: fid, itemIds: [user.id, item.id] });
      queueMicrotask(() => send(item.question));
    },
    [send],
  );

  const cancel = useCallback(() => abort("user"), [abort]);

  // A different field invalidates the running turn at once.
  useEffect(() => {
    if (active.current && active.current.fieldId !== fieldId) abort("field_changed");
  }, [fieldId, abort]);

  useEffect(() => () => abort("unmounted"), [abort]);

  // Stable identity unless something visible changed, so message components can skip re-renders.
  return useMemo(
    () => ({ thread, busy, backend, checking, refreshBackend, send, cancel, retry, applyAction, goBack, confirmConstraints, dismissConstraint }),
    [thread, busy, backend, checking, refreshBackend, send, cancel, retry, applyAction, goBack, confirmConstraints, dismissConstraint],
  );
}

export type AssistantController = ReturnType<typeof useAssistant>;
