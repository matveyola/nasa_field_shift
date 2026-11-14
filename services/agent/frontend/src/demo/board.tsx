// One board with every part of the assistant, each numbered, so the owner can say which ones to redo.
// Every tile is the real widget (its own CSS and components), replaying answers recorded from the live
// Gemini service (src/demo/board/*.sse, recorded 2026-11-12). Nothing here talks to a server.
import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/onest"; // the composer keeps its Onest
import "@fontsource-variable/inter/opsz.css";
import { type FieldContext, FieldShiftAssistant } from "../widget";
import { FieldMark } from "../widget/ui/icons";
import { demoCapabilities } from "./hostActions";
import { initialFields } from "./fields";
import recorded from "./board/index.json";
import "./board.css";

const sse = import.meta.glob("./board/*.sse", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const index = recorded as Record<string, string>;
const byText = new Map(Object.entries(index).map(([key, text]) => [text, sse[`./board/${key}.sse`]!]));
const HANG = "Чи буде дощ на вихідних?"; // a turn that stays in progress: the steps the farmer sees while waiting

const HEALTH = {
  status: "ready", agent: "llm/gemini", model: "gemini-3.8-flash", knowledge: { mode: "lexical" },
  integrations: { simulation: { configured: "http", connected: true, service_mode: "stub", compare_rotations: true, scenarios: ["typical"] } },
};

// Answers come from the recordings; the "offline" tile's base URL refuses like a stopped server.
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (url.includes("/offline/")) throw new TypeError("Failed to fetch");
  if (url.endsWith("/health/ready")) return Response.json(HEALTH);
  if (url.endsWith("/v1/agent/stream")) {
    const text = (JSON.parse(String(init?.body)) as { message: { text: string } }).message.text;
    const enc = new TextEncoder();
    if (text === HANG) {
      const head = byText.get(index.field!)!.split("\n\n").slice(0, 5).join("\n\n") + "\n\n"; // started, tools, no text yet
      return new Response(new ReadableStream({ start: (c) => c.enqueue(enc.encode(head)) }), { headers: { "content-type": "text/event-stream" } });
    }
    const body = byText.get(text);
    if (!body) return new Response("no recording for this text", { status: 500 });
    return new Response(body, { headers: { "content-type": "text/event-stream; charset=utf-8" } });
  }
  return realFetch(input, init);
};

const fields = initialFields();
const poltava = Object.values(fields)[0]!;
const ctx: FieldContext = { ...poltava.ctx, locale: "uk" };
const caps = demoCapabilities(true);

interface TileSpec {
  n: string;
  title: string;
  parts?: string[];
  open?: boolean;
  ask?: string;
  field?: boolean;
  offline?: boolean;
  /** CSS selector clicked once the answer is in (opens a source or the data list). */
  click?: string;
  keep?: boolean;
}

const TILES: TileSpec[] = [
  { n: "1", title: "Кнопка запуску", parts: ["1а чорна кнопка зі знаком у куті"] },
  { n: "2", title: "Порожній екран", open: true,
    parts: ["2а шапка: знак, назва, «Підключено», кругла кнопка закриття", "2б картка поля: назва, «Приклад», примітка про демо й ШІ", "2в привітання", "2г питання-підказки", "2д поле вводу (лишається як є)",
             "2е кругла чорна кнопка внизу: закриває вікно"] },
  { n: "3", title: "Поле не вибрано", open: true, field: false, parts: ["3а смужка «Поле не обрано»", "3б підказка вибрати поле"] },
  { n: "4", title: "Помічник недоступний", open: true, offline: true, parts: ["4а картка з повідомленням про збій", "4б стан у шапці"] },
  { n: "5", title: "Помічник працює", ask: HANG, parts: ["5а повідомлення фермера", "5б картка з кроками роботи й часом; дуга навколо знака в шапці", "5в кнопка «Стоп» у полі вводу"] },
  { n: "6", title: "Відповідь про поле", ask: index.field,
    parts: ["6а згорнуті кроки", "6б текст відповіді (оцінка й розрахунок позначені коротко)", "6в сіра плашка «демо-числа»", "6г «Використані дані»", "6д «Зберегти звіт для агронома»", "6е картки з числами: назва, звідки, велике число"] },
  { n: "7", title: "«Використані дані» розгорнуто", ask: index.field, click: ".fsa-evidence > summary",
    parts: ["7а рядок показника: назва і число", "7б позначки «Супутникові дані», «Демо-дані»", "7в джерело й період"] },
  { n: "8", title: "Погода й обприскування", ask: index.weather, parts: ["8а дні картками (таблиця з трьох і більше стовпців)", "8б рядок «Вікно для обприскування»", "8в текст відповіді"] },
  { n: "9", title: "Вода від дня сівби", ask: index.season, parts: ["9а текст без чисел", "9б картки з числами", "9в «Ваші умови»: пропозиція, «Підтвердити» / «Відхилити»"] },
  { n: "10", title: "Порівняння сівозмін", ask: index.compare,
    parts: ["10а варіанти рядками-картками", "10б примітка під таблицею", "10в сіра плашка «демо-числа»", "10г «Запропоновані дії»: «Відкрити порівняння»", "10д кнопки інших умов (посуха, спека), якщо є"] },
  { n: "11", title: "Порада з джерела", ask: index.knowledge, parts: ["11а кружечки з номером ① у тексті", "11б «Джерела (2)» під відповіддю"] },
  { n: "12", title: "Джерело відкрито", ask: index.knowledge, click: ".fsa-source, .fsa-cite",
    parts: ["12а «‹ Джерело 1» і позначка «Наукова література»", "12б назва документа й установа", "12в підрозділ і сторінка", "12г точна цитата", "12д посилання на PDF (нижче)"] },
  { n: "13", title: "Відкрити екран застосунку", ask: index.screen, parts: ["13а «Запропоновані дії»: «Погода · Застосовано автоматично»", "13б кнопка «Повернути як було»"] },
  { n: "14", title: "Ваші умови", ask: index.constraints, parts: ["14а текст відповіді (ці умови вже є в полі, тому кнопок немає; кнопки умов див. 9в)"] },
  { n: "15", title: "Показати на карті", ask: index.map, parts: ["15а «Шар вологості ґрунту · Застосовано автоматично»", "15б «Повернути як було»"] },
];

function Tile({ spec }: { spec: TileSpec }) {
  const box = useRef<HTMLDivElement>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  useEffect(() => setHost(box.current), []);
  useEffect(() => {
    if (!host || !(spec.open || spec.ask)) return;
    let stop = false;
    const wait = async (fn: () => boolean, ms = 6000) => {
      for (let t = 0; t < ms && !stop; t += 100) { if (fn()) return true; await new Promise((r) => setTimeout(r, 100)); }
      return false;
    };
    (async () => {
      await wait(() => !!host.querySelector(".fsa-launcher"));
      (host.querySelector(".fsa-launcher") as HTMLButtonElement | null)?.click();
      if (!spec.ask) return;
      const ta = () => host.querySelector("textarea") as HTMLTextAreaElement | null;
      await wait(() => !!ta() && !ta()!.disabled);
      const el = ta()!;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(el, spec.ask);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      await wait(() => !(host.querySelector(".fsa-sendbtn") as HTMLButtonElement | null)?.disabled);
      (host.querySelector(".fsa-sendbtn") as HTMLButtonElement | null)?.click();
      if (spec.click) {
        await wait(() => !!host.querySelector(spec.click!), 8000);
        const el = host.querySelector(spec.click) as HTMLElement | null;
        el?.click();
        el?.scrollIntoView({ block: "start" }); // inside the panel; the page itself goes back to the top below
      }
    })();
    return () => { stop = true; };
  }, [host, spec]);
  return (
    <figure className="tile">
      <figcaption><b>{spec.n}</b> {spec.title}</figcaption>
      <div className="stage" ref={box}>
        {host && (
          <FieldShiftAssistant
            container={host}
            transport={{ baseUrl: spec.offline ? "/offline" : "/board" }}
            fieldContext={spec.field === false ? null : ctx}
            fieldName={poltava.name.uk}
            locale="uk"
            capabilities={caps}
            onAction={() => ({ status: "applied" })}
          />
        )}
      </div>
      {spec.parts && <ol className="parts">{spec.parts.map((p) => <li key={p}>{p}</li>)}</ol>}
    </figure>
  );
}

const MARKS: [string, string][] = [["fsa-mark", "16а знак у шапці"], ["fsa-mark fsa-mark--busy", "16б працює: дуга обертається"]];

function Board() {
  // Each opened panel takes focus and scrolls the page to itself; once all have answered, start at the top.
  useEffect(() => {
    const t = setTimeout(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0); }, 4000);
    return () => clearTimeout(t);
  }, []);
  return (
    <main className="board">
      <h1>Усі частини помічника</h1>
      <p className="lead">
        Кожна плитка — справжній віджет у новому чорно-білому вигляді, з відповідями, записаними з Gemini 12.11.2026. Під
        плиткою перелічено її частини з номерами: напишіть, які номери переробити і як. Поле вводу (2д) лишається як є.
      </p>
      <div className="grid">{TILES.map((t) => <Tile key={t.n} spec={t} />)}</div>
      <section className="extra">
        <figure className="tile wide">
          <figcaption><b>16</b> Знак помічника</figcaption>
          <div className="moods fsa-root">
            {MARKS.map(([cls, label]) => (
              <div key={cls}><span className={`${cls} fsa-header-mark`}><FieldMark /></span><span>{label}</span></div>
            ))}
          </div>
        </figure>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><Board /></StrictMode>);
