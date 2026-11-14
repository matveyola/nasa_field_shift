import type { FieldContext, Job, WaterResult } from '../api/contracts'

export function isWaterResult(result: Job['result']): result is WaterResult {
  return result !== null && 'threshold_mm' in result && 'scenario' in result && !('analysis' in result)
}

export function matchingWaterResult(job: Job | undefined, context: FieldContext, boundaryRevision = 0) {
  if (job?.kind !== 'simulation' || job.status !== 'succeeded' || !isWaterResult(job.result)) return undefined
  if ((job.boundary_revision ?? 0) !== boundaryRevision) return undefined
  const result = job.result
  return result.field_id === context.field_id && result.scenario === context.scenario && result.season === context.season
    ? result : undefined
}
