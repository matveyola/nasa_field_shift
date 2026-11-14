import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FieldShiftAssistant, type ActionRequest, type FieldContext, type Locale, type TransportOptions } from "../widget";
import { dictionaries, label } from "../widget/i18n/strings";
import { FieldMark } from "../widget/ui/icons";
import { ComparisonCard, scenarioLabel } from "./ComparisonCard";
import { initialFields } from "./fields";
import { applyHostAction, DEMO_SCREENS, demoCapabilities, type LogEntry } from "./hostActions";
import { MapPreview } from "./MapPreview";
import { type FetchedResult, fetchResult, loadProfile, type ProfileStatus, simulationConnected } from "./services";

// Two local backends behind the Vite proxy (see vite.config.ts and README):
//   /agent      → AGENT_AGENT=deterministic  (rule-based: field facts, constraints, map actions)
//   /agent-llm  → AGENT_AGENT=scripted-llm   (real LLM loop and FAO-56 search with a scripted fake model)
//   /agent-bff  → dev reference host backend that signs each request (dev/referenceBff.ts) → scripted agent
//                 started with AGENT_REQUIRE_CONTEXT_ATTESTATION=true (M6C)
const BACKENDS = {
  rules: { baseUrl: "/agent", label: { uk: "Правила (M1)", en: "Rules (M1)" } },
  scripted: { baseUrl: "/agent-llm", label: { uk: "Сценарна LLM + FAO-56", en: "Scripted LLM + FAO-56" } },
  signed: { baseUrl: "/agent-bff", label: { uk: "Підписані запити (еталонний BFF)", en: "Signed requests (reference BFF)" } },
} as const;
type BackendKey = keyof typeof BACKENDS;

const T = {
  uk: {
    demo: "Демо-хост",
    synthetic: "Усі дані синтетичні",
    field: "Поле",
    none: "Поле не обрано",
    backend: "Сервіс агента",
    language: "Мова",
    layers: "Шари карти",
    facts: "Показники поля",
    conditions: "Умови фермера",
    scenario: "Сценарій",
    log: "Журнал дій хоста",
    logEmpty: "Ще немає дій від помічника.",
    noConditions: "Немає умов.",
    illustrative: "Ілюстративний вигляд шару: значення даних не показуються",
    noField: "Оберіть поле, щоб побачити його на карті",
    point: "Точкова геометрія",
    rev: "ред.",
    proposed: "запропоновано",
    confirmed: "підтверджено",
    rejected: "відхилено",
    applied: "застосовано",
    reset: "Скинути демо",
    services: "Сервіси даних",
    profile: "Профіль поля",
    profileLoaded: "отримано з гео-сервісу ({mode}, {n} показників)",
    profileMissing: "гео-сервіс недоступний ({reason}): показано вбудований тестовий контекст",
    simulation: "Моделювання сівозмін",
    simOn: "тестовий сервіс контракту (синтетичні результати)",
    simLive: "справжній розрахунок (лише поля, які сервіс знає)",
    simOff: "не підключено: порівняння недоступне",
    cmp: { title: "Порівняння сівозмін", synthetic: "Синтетичні тестові результати", computed: "розраховано", expires: "дійсне до",
           engine: "рушій", rank: "Місце", selected: "обрано", simulation: "Результат моделювання, не спостереження",
           forRevision: "розраховано для ред.", olderRevision: "поле змінилося після розрахунку",
           water_risk: "Ризик дефіциту води", soil_cover_days: "Днів із покривом", n_input: "Азот", yield_risk: "Ризик для врожаю",
           yield_loss_water: "Втрата врожаю через нестачу води" },
  },
  en: {
    demo: "Demo host",
    synthetic: "All data is synthetic",
    field: "Field",
    none: "No field selected",
    backend: "Agent service",
    language: "Language",
    layers: "Map layers",
    facts: "Field facts",
    conditions: "Farmer conditions",
    scenario: "Scenario",
    log: "Host action log",
    logEmpty: "No actions from the assistant yet.",
    noConditions: "No conditions.",
    illustrative: "Illustrative layer preview: no data values are drawn",
    noField: "Select a field to see it on the map",
    point: "Point geometry",
    rev: "rev.",
    proposed: "proposed",
    confirmed: "confirmed",
    rejected: "rejected",
    applied: "applied",
    reset: "Reset demo",
    services: "Data services",
    profile: "Field profile",
    profileLoaded: "loaded from the geo service ({mode}, {n} facts)",
    profileMissing: "geo service unavailable ({reason}): showing the bundled test context",
    simulation: "Rotation simulation",
    simOn: "contract test service (synthetic results)",
    simLive: "real computation (only the fields the service knows)",
    simOff: "not connected: comparisons unavailable",
    cmp: { title: "Rotation comparison", synthetic: "Synthetic test results", computed: "computed", expires: "valid until",
           engine: "engine", rank: "Rank", selected: "selected", simulation: "Simulation output, not an observation",
           forRevision: "computed for rev.", olderRevision: "the field changed after this computation",
           water_risk: "Water-deficit risk", soil_cover_days: "Days with cover", n_input: "Nitrogen", yield_risk: "Yield risk",
           yield_loss_water: "Harvest lost to water shortage" },
  },
};

// What the page shows. Default: a blank white page with only the assistant (the host logic still runs
// underneath: the field, its map state and the actions the assistant applies). ?host=full: the white demo
// host page with the map, field facts and action log. ?host=dark: that page in its original dark look.
export function App() {
  const params = new URLSearchParams(location.search);
  const view = params.get("host") === "full" || params.get("host") === "dark" ? "full" : "blank";
  const [locale, setLocale] = useState<Locale>(params.get("lang") === "en" ? "en" : "uk");
  const [backend, setBackend] = useState<BackendKey>(
    (Object.keys(BACKENDS) as BackendKey[]).find((k) => k === params.get("backend")) ?? "rules",
  );
  const [fields, setFields] = useState(initialFields);
  const [fieldId, setFieldId] = useState<string | null>(params.get("field") === "none" ? null : Object.keys(fields)[0]!);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [focusKey, setFocusKey] = useState(0);
  const [simMode, setSimMode] = useState<string | null>(null);
  const simOn = simMode !== null;
  const [profile, setProfile] = useState<Record<string, ProfileStatus>>({});
  const [shown, setShown] = useState<FetchedResult | null>(null);
  // Results this host fetched and checked itself; actions may only reference these.
  const results = useRef(new Map<string, FetchedResult>());
  // The blank view has no map and no comparison panel: it must not accept (and report as applied) their actions.
  const caps = useMemo(() => {
    const all = demoCapabilities(simOn);
    return view === "blank" ? { ...all, actions: all.actions.filter((a) => !a.startsWith("map.") && a !== "panel.open") } : all;
  }, [simOn, view]);
  const t = T[locale];

  const field = fieldId ? fields[fieldId] : undefined;
  const fieldLog = log.filter((e) => e.fieldId === fieldId); // another field's actions never show here
  // The FieldContext the widget sees: the stored one, in the UI language.
  const ctx: FieldContext | null = useMemo(() => (field ? { ...field.ctx, locale } : null), [field, locale]);

  // Synchronous mirror of the store: actions are validated against the very latest state.
  const store = useRef({ fields, fieldId });
  store.current = { fields, fieldId };

  useEffect(() => {
    void simulationConnected().then(setSimMode);
  }, []);

  // Field selected: the host asks the geo service for the profile and merges it (the agent never does this for the host).
  useEffect(() => {
    if (!fieldId) return;
    const base = store.current.fields[fieldId];
    if (!base) return;
    let current = true;
    void loadProfile(base.ctx).then(({ ctx: merged, status }) => {
      if (!current) return; // the farmer switched fields meanwhile: never write field A's profile into field B
      setProfile((p) => ({ ...p, [fieldId]: status }));
      if (merged !== base.ctx) setFields((all) => ({ ...all, [fieldId]: { ...all[fieldId]!, ctx: merged } }));
    });
    return () => {
      current = false;
    };
  }, [fieldId]);

  // A result shown for one field is never shown for another.
  useEffect(() => setShown((s) => (s && s.field_id === fieldId ? s : null)), [fieldId]);

  const onAction = useCallback(
    async (req: ActionRequest) => {
      const a = req.envelope.action;
      if (a.type === "panel.open" && a.panel === "comparison" && a.ref_id && !results.current.has(a.ref_id)) {
        const fetched = await fetchResult(a.ref_id, req.fieldId); // unknown / expired / foreign ids come back null
        if (fetched) results.current.set(fetched.result_id, fetched);
      }
      const { fields: all, fieldId: current } = store.current;
      const live = current ? { ...all[current]!.ctx, locale } : null;
      const { result, next, openComparison } = applyHostAction(live, req, caps, results.current);
      if (openComparison) setShown(results.current.get(openComparison) ?? null);
      if (next && current) {
        const updated = { ...all, [current]: { ...all[current]!, ctx: next } };
        store.current = { fields: updated, fieldId: current };
        setFields(updated);
        if (req.envelope.action.type === "map.focus_field") setFocusKey((k) => k + 1);
      }
      setLog((l) =>
        [
          {
            fieldId: current ?? "",
            at: new Date().toLocaleTimeString(locale === "uk" ? "uk-UA" : "en-GB"),
            type: req.envelope.action.type,
            label: req.envelope.label,
            origin: req.origin,
            outcome: result.status,
            detail: result.status === "applied" ? (result.revision != null ? `→ ${t.rev} ${result.revision}` : "") : result.reason,
          } satisfies LogEntry,
          ...l,
        ].slice(0, 30),
      );
      return result;
    },
    [locale, t.rev, caps],
  );

  // The farmer can also change the map directly: that bumps the revision and makes older chips stale.
  const toggleLayer = (layerId: string) => {
    if (!fieldId || !field) return;
    const active = field.ctx.ui_state?.active_layer_ids ?? [];
    const nextCtx: FieldContext = {
      ...field.ctx,
      revision: field.ctx.revision + 1,
      ui_state: { ...field.ctx.ui_state, active_layer_ids: active.includes(layerId) ? active.filter((x) => x !== layerId) : [layerId] },
    };
    setFields({ ...fields, [fieldId]: { ...field, ctx: nextCtx } });
  };

  const transport: TransportOptions = useMemo(() => ({ baseUrl: BACKENDS[backend].baseUrl }), [backend]);
  const constraints = ctx?.constraints ?? [];
  const facts = (ctx?.facts ?? []).filter((f) => f.quantity || f.category).slice(0, 4);

  const assistant = (
    <FieldShiftAssistant
      key={backend}
      transport={transport}
      fieldContext={ctx}
      fieldName={field?.name[locale]}
      locale={locale}
      capabilities={caps}
      onAction={onAction}
      showRevision={view === "full"}
    />
  );
  if (view === "blank") return <div className="demo-blank">{assistant}</div>;

  return (
    <div className="demo">
      <header className="demo-top">
        <div className="demo-brand">
          <FieldMark className="demo-brand-mark" />
          <span className="demo-brand-name">Field Shift</span>
          <span className="demo-pill">{t.demo}</span>
          <span className="demo-pill demo-pill--warn">{t.synthetic}</span>
        </div>
        <div className="demo-controls">
          <label>
            <span>{t.field}</span>
            <select value={fieldId ?? ""} onChange={(e) => setFieldId(e.target.value || null)} data-testid="field-select">
              {Object.values(fields).map((f) => (
                <option key={f.ctx.field_id} value={f.ctx.field_id}>{f.name[locale]}</option>
              ))}
              <option value="">{t.none}</option>
            </select>
          </label>
          <label>
            <span>{t.backend}</span>
            <select value={backend} onChange={(e) => setBackend(e.target.value as BackendKey)} data-testid="backend-select">
              {(Object.keys(BACKENDS) as BackendKey[]).map((k) => (
                <option key={k} value={k}>{BACKENDS[k].label[locale]}</option>
              ))}
            </select>
          </label>
          <label>
            <span>{t.language}</span>
            <select value={locale} onChange={(e) => setLocale(e.target.value as Locale)} data-testid="locale-select">
              <option value="uk">Українська</option>
              <option value="en">English</option>
            </select>
          </label>
          <button type="button" className="demo-reset" onClick={() => { setFields(initialFields()); setLog([]); }}>{t.reset}</button>
        </div>
      </header>

      <main className="demo-grid">
        <section className="demo-card demo-card--map" aria-label={t.layers}>
          <div className="demo-card-head">
            <h2>{field ? field.name[locale] : t.none}</h2>
            {ctx && <span className="demo-rev" data-testid="revision">{t.rev} {ctx.revision}</span>}
          </div>
          <MapPreview ctx={ctx} focusKey={focusKey} labels={{ illustrative: t.illustrative, noField: t.noField, point: t.point }} />
          {ctx && (ctx.available_layers?.length ?? 0) > 0 && (
            <div className="demo-layers" role="group" aria-label={t.layers}>
              {ctx.available_layers!.map((l) => {
                const on = ctx.ui_state?.active_layer_ids?.includes(l.layer_id) ?? false;
                return (
                  <button key={l.layer_id} type="button" aria-pressed={on} className="demo-layer" onClick={() => toggleLayer(l.layer_id)} data-testid={`layer-${l.layer_id}`}>
                    <span className="demo-layer-dot" data-layer={l.layer_id} />
                    {label(dictionaries[locale], l.label_key)}
                    <span className="demo-layer-kind">{l.provenance.kind === "ml_estimate" ? "model" : "EO"}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <section className="demo-card">
          <h2>{t.facts}</h2>
          {facts.length ? (
            <ul className="demo-facts">
              {facts.map((f) => (
                <li key={f.id}>
                  <span>{label(dictionaries[locale], f.label_key)}</span>
                  <strong>{f.quantity ? `${f.quantity.value} ${f.quantity.unit === "1" ? "" : f.quantity.unit}` : f.category}</strong>
                  <em>{f.provenance.source?.product_id ?? f.provenance.model?.name ?? f.provenance.kind} · {f.provenance.data_status}</em>
                </li>
              ))}
            </ul>
          ) : (
            <p className="demo-muted">—</p>
          )}
        </section>

        <section className="demo-card">
          <h2>{t.conditions}</h2>
          {constraints.length ? (
            <ul className="demo-conditions" data-testid="conditions">
              {constraints.map((c) => (
                <li key={c.id} data-status={c.status}>
                  <span>{c.type.replace(/_/g, " ")}: <strong>{JSON.stringify(c.value)}</strong></span>
                  <em>{t[c.status]}</em>
                </li>
              ))}
            </ul>
          ) : (
            <p className="demo-muted">{t.noConditions}</p>
          )}
          {ctx?.ui_state?.screen_id && (
            <p className="demo-muted" data-testid="screen">Екран: <strong>{DEMO_SCREENS.find((s) => s.screen_id === ctx.ui_state?.screen_id)?.label ?? ctx.ui_state.screen_id}</strong></p>
          )}
          {ctx?.ui_state?.scenario && (
            <p className="demo-muted">{t.scenario}: <strong data-testid="scenario" data-scenario={ctx.ui_state.scenario}>{scenarioLabel(ctx.ui_state.scenario, locale)}</strong></p>
          )}
        </section>

        <section className="demo-card">
          <h2>{t.services}</h2>
          <p className="demo-muted" data-testid="profile-status">
            {t.profile}:{" "}
            {fieldId && profile[fieldId]
              ? profile[fieldId]!.state === "loaded"
                ? t.profileLoaded.replace("{mode}", (profile[fieldId] as { mode: string }).mode).replace("{n}", String((profile[fieldId] as { facts: number }).facts))
                : t.profileMissing.replace("{reason}", (profile[fieldId] as { reason: string }).reason)
              : "…"}
          </p>
          <p className="demo-muted" data-testid="simulation-status">{t.simulation}: {simMode === "live" ? t.simLive : simOn ? t.simOn : t.simOff}</p>
        </section>

        {shown && ctx && (
          <ComparisonCard result={shown} selected={ctx.ui_state?.selected_rotation_ids ?? []} revision={ctx.revision} locale={locale} labels={t.cmp} />
        )}

        <section className="demo-card demo-card--log">
          <h2>{t.log}{field ? ` · ${field.name[locale]}` : ""}</h2>
          {fieldLog.length ? (
            <ol className="demo-log" data-testid="action-log" data-field={fieldId ?? ""}>
              {fieldLog.map((e, i) => (
                <li key={i} data-outcome={e.outcome}>
                  <span className="demo-log-time">{e.at}</span>
                  <code>{e.type}</code>
                  <span className="demo-log-label">{e.label}</span>
                  <span className="demo-log-outcome">{e.outcome === "applied" ? t.applied : t.rejected} {e.detail}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="demo-muted">{t.logEmpty}</p>
          )}
        </section>
      </main>

      {assistant}
    </div>
  );
}
