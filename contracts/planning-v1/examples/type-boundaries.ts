import type { CropRef, Metric, SuitabilityAssessment, ApplyPlanRequest, TrainingObservation } from '../../../apps/web/src/api/planningV1.generated';
import { example } from './roundtrip.generated';

export const rice: CropRef = { crop_id: 'Rice', cultivar_id: null, profile_version: null };
export const unknown: CropRef = { crop_id: 'unseen-hybrid-crop', cultivar_id: null, profile_version: null };
export const unavailable: Metric = example.evaluation.seasons[0].yield_t_ha;
// @ts-expect-error model training status must not be used as crop suitability
export const wrongStatus: SuitabilityAssessment = { ...example.suitability, status: 'trained' };
// @ts-expect-error applying a plan always needs an explicit human confirmation
export const unconfirmed: ApplyPlanRequest = { ...example.apply_request, confirmed_by_human: false };
// @ts-expect-error moisture unit is explicit; percent is not silently m3/m3
export const wrongUnit: TrainingObservation = { ...example.observation, unit: '%' };
