import type { PlanningDraft } from '../../api/rotationPlanning'

/** Summaries use only saved assignments, including failed searches with no decision. */
export function nextSeasonDecisions(draft: PlanningDraft, focusedField?: string) {
  return draft.fields.map(field => ({
    field,
    assignment: draft.result.assignments.find(row => row.field_id === field.id && row.season === draft.start_season),
    focused: field.id === focusedField,
  })).sort((a, b) => Number(b.focused) - Number(a.focused))
}

export function allocationSeasonSupported(season: number) {
  return Number.isInteger(season) && season >= 2001 && season <= 2100
}

export function initialStrategyView(focus?: 'profiles' | 'economics') {
  return focus ? 'scenarios' : 'allocation'
}
