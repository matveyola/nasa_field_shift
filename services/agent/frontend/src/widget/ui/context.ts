import { createContext, useContext } from "react";
import type { CropOption } from "../actions/editing";
import type { HostCapabilities } from "../actions/policy";
import type { FieldContext } from "../contracts";
import { type Dict, dictionaries, type Locale } from "../i18n/strings";
import type { AssistantController } from "../state/useAssistant";

export interface WidgetEnv {
  d: Dict;
  locale: Locale;
  /** The agent's display name: the agentName prop, or the dictionary default. */
  name: string;
  ctx: FieldContext | null;
  caps: HostCapabilities;
  trustedSourceHosts: readonly string[];
  /** The crops the host app plans with: the only crops a farmer can choose when editing a proposal. */
  crops: readonly CropOption[];
  assistant: AssistantController;
  openCitation(citationKey: string, trigger: HTMLElement): void;
}

export const Env = createContext<WidgetEnv | null>(null);

export function useEnv(): WidgetEnv {
  const env = useContext(Env);
  if (!env) throw new Error("Field Shift widget component rendered outside <FieldShiftAssistant>");
  return env;
}

export const fallbackDict = dictionaries.en;
