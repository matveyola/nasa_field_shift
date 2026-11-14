import { forwardRef } from "react";
import * as I from "./icons";

interface LauncherProps {
  open: boolean;
  busy: boolean;
  reducedMotion: boolean;
  label: string;
  panelId: string;
  onToggle(): void;
}

/**
 * Floating bottom-corner launcher: a black disc with the field mark (a chevron while open; a turning arc while the
 * assistant works); the panel unfolds from it. Two slow
 * pulse rings play once each time the launcher appears, about 4.8 s (transform + opacity only, so they run
 * on the compositor and never trigger layout or paint of the map underneath). They are not rendered while
 * the panel is open, nor at all under prefers-reduced-motion.
 */
export const Launcher = forwardRef<HTMLButtonElement, LauncherProps>(function Launcher(
  { open, busy, reducedMotion, label, panelId, onToggle },
  ref,
) {
  return (
    <button
      ref={ref}
      type="button"
      className={`fsa-launcher${open ? " fsa-launcher--open" : ""}${busy ? " fsa-launcher--busy" : ""}`}
      aria-label={label}
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      aria-haspopup="dialog"
      onClick={onToggle}
      data-motion={reducedMotion ? "reduced" : "full"}
    >
      {!reducedMotion && !open && (
        <span className="fsa-pulse" aria-hidden="true">
          <span />
          <span />
        </span>
      )}
      <span className="fsa-launcher-core" aria-hidden="true">
        <I.FieldMark className="fsa-launcher-mark" />
        <I.ChevronDown className="fsa-launcher-close" />
      </span>
    </button>
  );
});
