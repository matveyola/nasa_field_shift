import type { FetchedResult } from "./services";

// The host's comparison view (owned by members 4/5 in the real app). It shows only a result the host
// fetched by id from the simulation service and checked: field, status, expiry, contract.

const METRICS = ["water_risk", "soil_cover_days", "n_input", "yield_risk", "yield_loss_water"] as const;

// Display names only: the scenario id itself stays unchanged in contracts, actions, checks and requests.
const SCENARIOS: Record<"uk" | "en", Record<string, string>> = {
  uk: { typical: "Типові умови", drought_2024: "Посуха (як у 2024)", heat_plus_2_5c: "Спека +2,5 °C",
        driest_recent_year: "Найсухіший із недавніх років", hottest_recent_year: "Найспекотніший із недавніх років" },
  en: { typical: "Typical conditions", drought_2024: "Drought (like 2024)", heat_plus_2_5c: "Heat +2.5 °C",
        driest_recent_year: "Driest recent year", hottest_recent_year: "Hottest recent year" },
};

/** Localized scenario name; an id we have no name for is shown as it is. */
export function scenarioLabel(id: string, locale: "uk" | "en"): string {
  return Object.hasOwn(SCENARIOS[locale], id) ? SCENARIOS[locale][id]! : id;
}
const UNIT: Record<string, string> = { "%": "%", d: " d", "kg/ha": " kg/ha" };

export function ComparisonCard({ result, selected, revision, locale, labels }: {
  result: FetchedResult;
  locale: "uk" | "en";
  selected: readonly string[];
  revision: number;
  labels: Record<"title" | "synthetic" | "computed" | "expires" | "engine" | "rank" | "selected" | "simulation" | "forRevision" | "olderRevision" | (typeof METRICS)[number], string>;
}) {
  const r = result.response;
  const synthetic = r.service.mode !== "live";
  // Only the measures this service computed: a column of dashes would read as "zero" or "missing data".
  const metrics = METRICS.filter((m) => r.comparison?.rotations.some((rot) => rot.scores[m]));
  return (
    <section className="demo-card demo-card--comparison" data-testid="comparison" data-scenario={r.scenario} aria-label={labels.title}>
      <div className="demo-card-head">
        <h2>{labels.title} · {scenarioLabel(r.scenario, locale)}</h2>
        {synthetic && <span className="demo-pill demo-pill--warn">{labels.synthetic}</span>}
      </div>
      <p className="demo-muted">
        {labels.simulation} · {labels.engine}: {r.engine.name} {r.engine.version} · {r.service.implementation} ({r.service.mode})<br />
        {labels.computed}: {r.computed_at} · {labels.expires}: {r.expires_at} · <code>{r.result_id}</code>
        <br />
        {labels.forRevision} {r.field_context_revision}
        {r.field_context_revision < revision && <strong className="demo-warn"> · {labels.olderRevision}</strong>}
      </p>
      {/* Scrolls sideways instead of squeezing columns on narrow screens; focusable so keyboards can scroll it too. */}
      <div className="demo-table-wrap" role="region" aria-label={labels.title} tabIndex={0} data-testid="comparison-table">
      <table className="demo-table">
        <thead>
          <tr>
            <th scope="col">{labels.rank}</th>
            <th scope="col">Rotation</th>
            {metrics.map((m) => <th key={m} scope="col">{labels[m]}</th>)}
          </tr>
        </thead>
        <tbody>
          {[...(r.comparison?.rotations ?? [])].sort((a, b) => a.rank - b.rank).map((rot) => (
            <tr key={rot.rotation_id} data-selected={selected.includes(rot.rotation_id) || undefined}>
              <td>{rot.rank}</td>
              <th scope="row">
                {rot.rotation_id}
                {selected.includes(rot.rotation_id) && <span className="demo-pill">{labels.selected}</span>}
                <small>{rot.sequence.map((s) => s.crop_id.replace(/_/g, " ")).join(" → ")}</small>
              </th>
              {metrics.map((m) => {
                const s = rot.scores[m];
                if (!s) return <td key={m}>—</td>;
                const u = s.uncertainty;
                const spread = u && u.low != null && u.high != null ? ` (${u.low}–${u.high})` : u?.sd != null ? ` ± ${u.sd}` : "";
                return (
                  <td key={m}>
                    {s.value}{UNIT[s.unit] ?? ` ${s.unit}`}{spread}
                    <small>{s.provenance.kind === "ml_estimate" ? "ML estimate" : "calculated"}{s.provenance.data_status === "fixture" ? " · fixture" : ""}</small>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  );
}
