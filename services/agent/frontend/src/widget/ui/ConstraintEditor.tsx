import { type FormEvent, type KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { editConstraint, type EditError, editorKind, FERTILIZER_BUDGETS, MAX_TEXT, ROTATION_YEARS } from "../actions/editing";
import type { Constraint } from "../contracts";
import { fmt } from "../i18n/strings";
import { useEnv } from "./context";

interface Props {
  /** The value being edited: the proposal, or the farmer's earlier edit of it. */
  constraint: Constraint;
  /** The assistant's proposal (its crops are all on the host's shared list: see editBlock). */
  proposal: Constraint;
  /** The checked edit (nothing is saved: the farmer still confirms or rejects it). */
  onApply(edited: Constraint): void;
  onCancel(): void;
}

function initial(c: Constraint): unknown {
  return editorKind(c.type) === "years" ? String(c.value) : c.value;
}

/**
 * Edits a proposed condition with the control its type needs. The value is checked before it can be used (contract
 * limits, this app's crop list) and never saved here: the farmer decides it afterwards with Confirm or Reject.
 */
export function ConstraintEditor({ constraint, proposal, onApply, onCancel }: Props) {
  const { d, crops } = useEnv();
  const t = d.constraints;
  const id = useId();
  const [value, setValue] = useState<unknown>(() => initial(constraint));
  const [error, setError] = useState<EditError | null>(null);
  const first = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  useEffect(() => first.current?.focus(), []);

  const kind = editorKind(constraint.type);
  const errorId = `${id}-error`;
  const invalid = error ? { "aria-invalid": true, "aria-describedby": errorId } : {};

  function submit(event: FormEvent) {
    event.preventDefault();
    const result = editConstraint(proposal, value, crops);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    onApply(result.constraint);
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== "Escape") return;
    event.stopPropagation(); // closes the editor, not the assistant
    onCancel();
  }

  const choice = (options: readonly { value: unknown; label: string }[], name: string) => (
    <div className="fsa-edit-options" role="radiogroup" aria-labelledby={`${id}-legend`} {...invalid}>
      {options.map((option, i) => (
        <label key={String(option.value)} className="fsa-edit-option">
          <input
            ref={i === 0 ? (el) => { first.current = el; } : undefined}
            type="radio"
            name={name}
            checked={value === option.value}
            onChange={() => { setValue(option.value); setError(null); }}
          />
          {option.label}
        </label>
      ))}
    </div>
  );

  let control;
  switch (kind) {
    case "boolean": {
      const labels = d.constraintText[constraint.type as "irrigation_available" | "erosion_protection" | "organic_only"];
      control = choice([{ value: true, label: labels.true }, { value: false, label: labels.false }], `${id}-bool`);
      break;
    }
    case "fertilizer":
      control = choice(FERTILIZER_BUDGETS.map((budget) => ({ value: budget, label: d.constraintText.fertilizer_budget[budget] })), `${id}-budget`);
      break;
    case "years":
      control = (
        <label className="fsa-edit-field">
          {fmt(t.yearsLabel, { min: ROTATION_YEARS.min, max: ROTATION_YEARS.max })}
          <input
            ref={(el) => { first.current = el; }}
            type="number"
            inputMode="numeric"
            min={ROTATION_YEARS.min}
            max={ROTATION_YEARS.max}
            step={1}
            value={String(value ?? "")}
            onChange={(e) => { setValue(e.target.value); setError(null); }}
            {...invalid}
          />
        </label>
      );
      break;
    case "crops": {
      const chosen = Array.isArray(value) ? value : [];
      control = (
        <div className="fsa-edit-options" role="group" aria-label={t.cropsLabel} {...invalid}>
          {crops.map((crop, i) => (
            <label key={crop.id} className="fsa-edit-option">
              <input
                ref={i === 0 ? (el) => { first.current = el; } : undefined}
                type="checkbox"
                checked={chosen.includes(crop.id)}
                onChange={(e) => {
                  setValue(e.target.checked ? [...chosen, crop.id] : chosen.filter((v) => v !== crop.id));
                  setError(null);
                }}
              />
              {crop.label}
            </label>
          ))}
        </div>
      );
      break;
    }
    case "crop":
      control = choice(crops.map((crop) => ({ value: crop.id, label: crop.label })), `${id}-crop`);
      break;
    case "text":
      control = (
        <label className="fsa-edit-field">
          {t.textLabel}
          <textarea
            ref={(el) => { first.current = el; }}
            rows={2}
            maxLength={MAX_TEXT + 50}
            value={typeof value === "string" ? value : ""}
            onChange={(e) => { setValue(e.target.value); setError(null); }}
            {...invalid}
          />
        </label>
      );
      break;
  }

  return (
    <form className="fsa-edit" onSubmit={submit} onKeyDown={onKeyDown} noValidate>
      <fieldset>
        <legend id={`${id}-legend`}>{t.editorTitle}</legend>
        <p className="fsa-edit-hint">{t.editorHint}</p>
        {(kind === "crops" || kind === "crop") && <p className="fsa-edit-hint">{t.sharedCropsHint}</p>}
        {control}
        {error && <p id={errorId} className="fsa-note fsa-note--warn" role="alert">{t.errors[error]}</p>}
      </fieldset>
      <div className="fsa-row">
        <button type="submit" className="fsa-btn fsa-btn--primary fsa-btn--sm">{t.apply}</button>
        <button type="button" className="fsa-btn fsa-btn--ghost fsa-btn--sm" onClick={onCancel}>{t.cancelEdit}</button>
      </div>
    </form>
  );
}
