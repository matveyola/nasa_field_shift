import type { Constraint, Quantity } from "../contracts";
import { type Dict, fmt, type Locale } from "./strings";

const UNITS: Record<Quantity["unit"], string> = {
  "1": "",
  "%": "%",
  "[pH]": "",
  mm: "mm",
  "mm/d": "mm/d",
  "m3/m3": "m³/m³",
  Cel: "°C",
  "Cel.d": "°C·d",
  d: "d",
  m: "m",
  cm: "cm",
  ha: "ha",
  deg: "°",
  "kg/ha": "kg/ha",
  "t/ha": "t/ha",
  "g/kg": "g/kg",
  "cmol/kg": "cmol/kg",
  "MJ/m2/d": "MJ/m²/d",
  "m/s": "m/s",
};

// Ukrainian unit names, as the agent's own text writes them (agent_service/plain.py); others stay international.
const UNITS_UK: Partial<Record<Quantity["unit"], string>> = {
  mm: "мм",
  "mm/d": "мм/добу",
  "m3/m3": "м³/м³",
  "Cel.d": "°C·днів",
  m: "м",
  cm: "см",
  ha: "га",
  "kg/ha": "кг/га",
  "t/ha": "т/га",
  "g/kg": "г/кг",
  "cmol/kg": "смоль/кг",
  "MJ/m2/d": "МДж/м²/добу",
  "m/s": "м/с",
};

/** Days agree with the number: 1 день, 2 дні, 5 днів; 1 day, 2 days. */
function days(value: number, locale: Locale): string {
  const n = Math.abs(value);
  if (locale === "en") return n === 1 ? "day" : "days";
  if (!Number.isInteger(n)) return "дня";
  if (n % 10 === 1 && n % 100 !== 11) return "день";
  return n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "дні" : "днів";
}

const numberFormats = new Map<string, Intl.NumberFormat>();

/** Locale number formatting (uk uses a decimal comma), keeping the precision the data has. */
export function formatNumber(value: number, locale: Locale): string {
  const key = locale;
  let nf = numberFormats.get(key);
  if (!nf) {
    nf = new Intl.NumberFormat(locale === "uk" ? "uk-UA" : "en-GB", { maximumFractionDigits: 6 });
    numberFormats.set(key, nf);
  }
  return nf.format(value);
}

export function formatQuantity(q: Quantity, locale: Locale): string {
  const unit = q.unit === "d" ? days(q.value, locale) : ((locale === "uk" ? UNITS_UK[q.unit] : undefined) ?? UNITS[q.unit]);
  const value = formatNumber(q.value, locale);
  const u = q.uncertainty;
  let spread = "";
  if (u?.sd != null) spread = ` ± ${formatNumber(u.sd, locale)}`;
  else if (u?.low != null && u?.high != null) spread = ` (${formatNumber(u.low, locale)}–${formatNumber(u.high, locale)})`;
  return `${value}${unit && unit !== "%" && unit !== "°" ? " " : ""}${unit}${spread}`;
}

/** A crop id in everyday words ("maize" -> "кукурудза"); unknown ids as readable words. */
function crop(d: Dict, id: string): string {
  return d.crops[id] ?? id.replace(/_/g, " ");
}

export function constraintText(d: Dict, c: Constraint): string {
  const t = d.constraintText;
  switch (c.type) {
    case "irrigation_available":
    case "erosion_protection":
    case "organic_only":
      return t[c.type][String(c.value) as "true" | "false"];
    case "fertilizer_budget":
      return t.fertilizer_budget[c.value];
    case "rotation_length_years":
      return fmt(t.rotation_length_years, { v: c.value });
    case "excluded_crops":
    case "preferred_crops":
    case "market_preference":
      return fmt(t[c.type], { v: c.value.map((id) => crop(d, id)).join(", ") });
    case "previous_crop":
      return fmt(t.previous_crop, { v: crop(d, c.value) });
    case "machinery_limit":
      return fmt(t.machinery_limit, { v: c.value });
  }
}
