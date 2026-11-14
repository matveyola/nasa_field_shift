// The demo host's "store": the M0 fixture field contexts, unchanged. Every value in them is synthetic. Marsden Farm has
// no values of its own: it is the field the Marsden rotation service knows (python -m integration_stub --sim-marsden).
import { type FieldContext, fieldContextProblem } from "../widget";
import kansas from "../../../contracts/fixtures/field_context.kansas.json";
import mykolaiv from "../../../contracts/fixtures/field_context.mykolaiv.json";
import poltava from "../../../contracts/fixtures/field_context.poltava.json";
import marsden from "../../../contracts/fixtures/field_context.marsden.json";

export interface DemoField {
  ctx: FieldContext;
  name: { uk: string; en: string };
}

/** Fixtures are checked against the M0 schema at load, like any data the host would receive. */
function asContext(json: unknown): FieldContext {
  const problem = fieldContextProblem(json);
  if (problem) throw new Error(`fixture is not a valid FieldContext: ${problem}`);
  const ctx = structuredClone(json) as FieldContext;
  return { ...ctx, ui_state: { screen_id: "field_map", ...ctx.ui_state } }; // the demo app opens on its map screen
}

export function initialFields(): Record<string, DemoField> {
  const list: DemoField[] = [
    { ctx: asContext(poltava), name: { uk: "Демо-поле під Полтавою", en: "Demo field near Poltava" } },
    { ctx: asContext(kansas), name: { uk: "Демо-поле в Канзасі", en: "Demo field in Kansas" } },
    { ctx: asContext(mykolaiv), name: { uk: "Демо-точка під Миколаєвом", en: "Demo point near Mykolaiv" } },
    { ctx: asContext(marsden), name: { uk: "Marsden Farm (Айова, США)", en: "Marsden Farm (Iowa, USA)" } },
  ];
  return Object.fromEntries(list.map((f) => [f.ctx.field_id, f]));
}
