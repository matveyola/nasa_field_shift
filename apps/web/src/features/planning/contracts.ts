import Ajv2020 from 'ajv/dist/2020.js'
import addFormats from 'ajv-formats'
import schema from '../../../../../contracts/planning-v1/planning-v1.schema.json' with { type: 'json' }
import type { PlanningContracts } from '../../api/planningV1.generated'

const ajv = new Ajv2020({ allErrors: true, strict: true, strictTuples: false })
addFormats(ajv)
ajv.addKeyword({ keyword: 'tsType', schemaType: 'string' })
ajv.addSchema(schema, 'planning-v1')
const validators = new Map<string, ReturnType<typeof ajv.compile>>()
const names = {
  field_profile: 'FieldProfileSnapshot', request: 'FarmPlanningRequest', run: 'PlanningRun',
  apply_result: 'ApplyPlanResult', profile_write: 'ProfileWriteRequest', replan: 'ReplanRequest',
  apply_request: 'ApplyPlanRequest', candidate: 'CandidateSequence', evaluation: 'SequenceEvaluation',
  error: 'PlanningError',
} as const
export type ContractKey = keyof typeof names

export class PlanningContractError extends Error {
  details: string
  constructor(message: string, details: string) { super(message); this.details = details }
}

export function decode<K extends ContractKey>(kind: K, value: unknown): PlanningContracts[K] {
  let validate = validators.get(kind)
  if (!validate) {
    validate = ajv.compile({ $ref: `planning-v1#/$defs/${names[kind]}` })
    validators.set(kind, validate)
  }
  if (!validate(value)) throw new PlanningContractError(
    ['request', 'profile_write', 'replan', 'apply_request'].includes(kind)
      ? 'Review required fields and units' : 'The server returned an incompatible planning result',
    ajv.errorsText(validate.errors),
  )
  return value as PlanningContracts[K]
}
