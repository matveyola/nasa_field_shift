// Original line icons (24px grid unless noted). Decorative: always aria-hidden.
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;
const base = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: false } as const;

/** The assistant's mark: one field parcel seen from above, with its crop rows. Drawn white on a black disc. */
export function FieldMark(props: P) {
  return (
    <svg viewBox="0 0 24 24" {...base} strokeWidth={1.7} {...props}>
      <path d="M3 8.5L16 5L21 11L18.5 18.5L5.5 17.5Z" />
      <path d="M8 9.4v6.2M12 8.5v7.4M16 7.7v8.5" strokeWidth={1.3} opacity={0.6} />
    </svg>
  );
}

export const Undo = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M9 14L4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></svg>);
export const ChevronDown = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M6 9l6 6 6-6" /></svg>);
export const Paperclip = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M15.5 7.5l-6.4 6.4a1.9 1.9 0 0 0 2.7 2.7l6.6-6.6a3.8 3.8 0 0 0-5.4-5.4L6.4 11.2a5.7 5.7 0 0 0 8.1 8.1l5-5" /></svg>);
export const Mic = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" /></svg>);
export const Close = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M6 6l12 12M18 6L6 18" /></svg>);
export const Send = (p: P) => (<svg viewBox="0 0 24 24" {...base} strokeWidth={1.9} {...p}><path d="M12 19V5.5M6 11.5l6-6 6 6" /></svg>);
export const Stop = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none" /></svg>);
export const Back = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M15 6l-6 6 6 6" /></svg>);
export const Check = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M5 12.5l4.2 4.2L19 7" /></svg>);
export const Screen = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M3.5 9h17" /></svg>);
export const Arrow = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M7 17L17 7M9 7h8v8" /></svg>);
export const Chevron = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M9 6l6 6-6 6" /></svg>);
export const Alert = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 4l9 16H3L12 4Z" /><path d="M12 10v4M12 17h.01" /></svg>);
export const Info = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></svg>);
export const Layers = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 4l8 4-8 4-8-4 8-4Z" /><path d="M4 12l8 4 8-4M4 16l8 4 8-4" /></svg>);
export const Chart = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M4 19h16M7 15l3-4 3 2 4-6" /></svg>);
export const Book = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M5 5.5A1.5 1.5 0 0 1 6.5 4H19v14H6.5A1.5 1.5 0 0 0 5 19.5v-14Z" /><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19" /></svg>);
export const Satellite = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M9.5 9.5l5 5M6 13l5 5M13 6l5 5" /><path d="M4 20c0-2.2 1.8-4 4-4" /><rect x="9" y="7.5" width="5" height="9" rx="1" transform="rotate(-45 11.5 12)" /></svg>);
export const Drop = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 4c3.5 4.2 5.5 7.3 5.5 10a5.5 5.5 0 0 1-11 0c0-2.7 2-5.8 5.5-10Z" /></svg>);
export const Sprout = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 20v-8" /><path d="M12 12c0-3.5-2.5-6-7-6 0 3.5 2.5 6 7 6ZM12 13c0-3 2.2-5.5 6.5-5.5 0 3-2.2 5.5-6.5 5.5Z" /></svg>);
export const Pin = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><path d="M12 21s6-5.6 6-11a6 6 0 0 0-12 0c0 5.4 6 11 6 11Z" /><circle cx="12" cy="10" r="2" /></svg>);
export const Scenario = (p: P) => (<svg viewBox="0 0 24 24" {...base} {...p}><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></svg>);
