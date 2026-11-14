// Types for the generated standalone Ajv validators (validators.js).
export interface ValidationError {
  instancePath: string;
  schemaPath: string;
  keyword: string;
  message?: string;
}
export interface Validator {
  (data: unknown): boolean;
  errors?: ValidationError[] | null;
}
export const validateAgentEvent: Validator;
export const validateAgentMessage: Validator;
export const validateAgentRequest: Validator;
export const validateErrorEnvelope: Validator;
export const validateFieldContext: Validator;
export const validateUIActionEnvelope: Validator;
export const validateComparisonResponse: Validator;
export const validateFieldProfileResponse: Validator;
