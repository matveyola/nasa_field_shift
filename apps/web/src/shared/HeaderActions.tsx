import { createContext, useContext, type ReactNode } from 'react'

/** App-owned controls may fill headers without coupling shared UI to a domain feature. */
export const HeaderActionsContext = createContext<ReactNode>(null)

export function HeaderActions() {
  return useContext(HeaderActionsContext)
}
