// Which saved water-balance run the assistant explains, kept apart from React so it can be tested on its own.
// Only a run the farmer explicitly selected (Water → saved run → «Ask agent») is ever explained; the assistant
// never picks the latest run itself. The API loads and checks the run (field, boundary, season, scenario,
// completed historical result) and signs the context it builds from it.
import type { Job, WaterResult } from '../../api/contracts'

/** The farmer's selection, with the field context it was made in. */
export type AssistantRun = { id: string; fieldId: string; season: number; scenario: string }

type Shown = { fieldId?: string; season?: number; scenario: string; page: string; section?: string; jobId?: string }

/** The selected run while it still matches what the app shows; otherwise none (it is stale, never replaced). */
export function currentAssistantRun(run: AssistantRun | undefined, shown: Shown): string | undefined {
  if (!run || run.fieldId !== shown.fieldId || run.season !== shown.season || run.scenario !== shown.scenario) return undefined
  // On the Water page another selected run (or none) replaces this one.
  if ((shown.page === 'water' || (shown.page === 'analytics' && shown.section === 'water')) && shown.jobId !== run.id) return undefined
  return run.id
}

/** The widget's base URL path: the widget appends /health/ready and /v1/agent/stream to it, so the run is a path segment. */
export function assistantPath(fieldId: string, season: number, scenario: string, runId?: string): string {
  const base = `/agent/assistant/${encodeURIComponent(fieldId)}/${season}/${encodeURIComponent(scenario)}`
  return runId ? `${base}/runs/${encodeURIComponent(runId)}` : base
}

type Conversation = { fieldId?: string; boundaryRevision?: number; season: number; scenario: string; runId?: string }

/**
 * One conversation per field context: another field, boundary revision, season, scenario or run starts a new one,
 * so no earlier answer (or its evidence) reaches a turn about different data. A JSON tuple, so no value can
 * run into the next (a field id containing a separator).
 */
export function conversationKey({ fieldId, boundaryRevision, season, scenario, runId }: Conversation): string {
  return JSON.stringify([fieldId ?? null, boundaryRevision ?? null, season, scenario, runId ?? null])
}

/**
 * The run «Ask agent» hands to the assistant on the Water page: the run in the address (the farmer chose it),
 * finished, showing its own historical result. The latest run shown by default is not a selection, and a demo
 * run is not historical evidence.
 */
export function explicitHistoricalRun(jobId: string | undefined, job: Job | undefined, shown: WaterResult | undefined): string | undefined {
  if (!jobId || job?.id !== jobId || job.status !== 'succeeded' || !shown || job.result !== shown) return undefined
  return !shown.demo && shown.source === 'historical' ? jobId : undefined
}

/** The period of the historical run in a server-built context, from its own provenance; undefined in any other context. */
export function historicalPeriod(context: Record<string, unknown> | null): { start: string; end: string } | undefined {
  const charts = Array.isArray(context?.available_charts) ? context.available_charts as { chart_id?: unknown }[] : []
  if (!charts.some((chart) => chart?.chart_id === 'c_water_season')) return undefined
  const series = Array.isArray(context?.timeseries_index) ? context.timeseries_index as { metric_id?: unknown; range?: { start?: unknown; end?: unknown } }[] : []
  const range = series.find((item) => item?.metric_id === 'm_rootzone_reserve')?.range
  return typeof range?.start === 'string' && typeof range.end === 'string' ? { start: range.start, end: range.end } : undefined
}
