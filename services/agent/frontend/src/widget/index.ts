// Public API of the Field Shift assistant widget.
export { FieldShiftAssistant, WIDGET_VERSION } from "./FieldShiftAssistant";
export type { FieldShiftAssistantProps, ThemeOverrides } from "./FieldShiftAssistant";
export { actionProblem, isCurrent, requiresClick } from "./actions/policy";
export type { ActionRequest, ActionResult, ActionType, HostCapabilities, KnownResult, RejectReason } from "./actions/policy";
export type { ActionHandler } from "./state/useAssistant";
export { checkBackend, sendMessage, streamTurn } from "./transport/client";
export type { BackendStatus, TransportOptions } from "./transport/client";
export { AgentError } from "./transport/errors";
export { SSEParser } from "./transport/sse";
export { checkComparisonResponse, fieldContextProblem } from "./contracts";
export type { Locale } from "./i18n/strings";
export { FieldMark } from "./ui/icons"; // the assistant's mark, for a host button that opens it
export type * from "./contracts";
