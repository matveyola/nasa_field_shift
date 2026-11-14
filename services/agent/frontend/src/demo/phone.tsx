// Phone preview: the blank demo page (only the assistant) inside a modern phone frame, at the real CSS size of a
// current iPhone screen (393 × 852 points). The page runs in an iframe of that width, so the widget's own phone
// layout switches on exactly as it would on the device. A preview, not an emulator: no Safari toolbars, no touch
// keyboard, and the frame is generic (no brand marks).
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/onest"; // the composer keeps its Onest
import "@fontsource-variable/inter/opsz.css";
import "./phone.css";

const SCREEN = { w: 393, h: 852 };
const BEZEL = 14;
const STATUS = 54; // status bar with the camera island
const HOME = 30; // home-indicator strip

const T = {
  uk: {
    title: "Помічник на телефоні",
    intro: "Так помічник виглядатиме на смартфоні (екран 393 × 852, як у сучасних iPhone). Натисніть зелену кнопку з жуком унизу праворуч.",
    language: "Мова",
    show: "Що показати",
    rules: "Поле й умови",
    scripted: "Наукові джерела",
    restart: "Почати заново",
    note: "Дані поля в демо вигадані для прикладу. Відповіді дає демо-програма, а не штучний інтелект.",
    frame: "Екран телефону з помічником",
  },
  en: {
    title: "The assistant on a phone",
    intro: "This is how the assistant looks on a smartphone (a 393 × 852 screen, like current iPhones). Tap the green button with the beetle at the bottom right.",
    language: "Language",
    show: "What to show",
    rules: "Field and conditions",
    scripted: "Scientific sources",
    restart: "Start over",
    note: "The field data in this demo is made up. Answers come from a demo program, not from AI.",
    frame: "Phone screen with the assistant",
  },
};

function fitScale() {
  const h = (window.innerHeight - 32) / (SCREEN.h + 2 * BEZEL);
  const w = (window.innerWidth - 32) / (SCREEN.w + 2 * BEZEL);
  return Math.min(1, h, w);
}

function Phone() {
  const params = new URLSearchParams(location.search);
  const [lang, setLang] = useState<"uk" | "en">(params.get("lang") === "en" ? "en" : "uk");
  const [mode, setMode] = useState<"rules" | "scripted">(params.get("backend") === "scripted" ? "scripted" : "rules");
  const [run, setRun] = useState(0);
  const [scale, setScale] = useState(fitScale);
  const [time, setTime] = useState(() => new Date());
  const t = T[lang];

  useEffect(() => {
    const onResize = () => setScale(fitScale());
    window.addEventListener("resize", onResize);
    const clock = window.setInterval(() => setTime(new Date()), 20_000);
    return () => {
      window.removeEventListener("resize", onResize);
      window.clearInterval(clock);
    };
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = T[lang].title;
  }, [lang]);

  const src = `/?lang=${lang}&backend=${mode}`;
  const clock = time.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });

  return (
    <main className="ph-stage">
      <section className="ph-side" aria-labelledby="ph-title">
        <h1 id="ph-title">{t.title}</h1>
        <p>{t.intro}</p>
        <label className="ph-field">
          <span>{t.language}</span>
          <select value={lang} onChange={(e) => setLang(e.target.value as "uk" | "en")} data-testid="phone-lang">
            <option value="uk">Українська</option>
            <option value="en">English</option>
          </select>
        </label>
        <fieldset className="ph-field">
          <legend>{t.show}</legend>
          {(["rules", "scripted"] as const).map((m) => (
            <label key={m} className="ph-radio">
              <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
              {t[m]}
            </label>
          ))}
        </fieldset>
        <button type="button" className="ph-restart" onClick={() => setRun((n) => n + 1)}>{t.restart}</button>
        <p className="ph-note">{t.note}</p>
      </section>

      <div className="ph-holder" style={{ width: (SCREEN.w + 2 * BEZEL) * scale, height: (SCREEN.h + 2 * BEZEL) * scale }}>
        <div className="ph-device" style={{ transform: `scale(${scale})`, padding: BEZEL }}>
          <div className="ph-screen" style={{ width: SCREEN.w, height: SCREEN.h }}>
            <div className="ph-status" style={{ height: STATUS }} aria-hidden="true">
              <span className="ph-clock">{clock}</span>
              <span className="ph-island" />
              <span className="ph-icons">
                <svg viewBox="0 0 18 12" width="18" height="12"><rect x="0" y="8" width="3" height="4" rx="1" /><rect x="5" y="5.5" width="3" height="6.5" rx="1" /><rect x="10" y="3" width="3" height="9" rx="1" /><rect x="15" y="0" width="3" height="12" rx="1" /></svg>
                <svg viewBox="0 0 16 12" width="16" height="12"><path d="M8 11.5l2.2-2.6a3.4 3.4 0 0 0-4.4 0Z M8 3.2a8.7 8.7 0 0 1 6.1 2.5l1.4-1.6A10.8 10.8 0 0 0 8 1 10.8 10.8 0 0 0 .5 4.1l1.4 1.6A8.7 8.7 0 0 1 8 3.2Zm0 3.2a5.3 5.3 0 0 1 3.7 1.5l1.4-1.6A7.5 7.5 0 0 0 8 4.3 7.5 7.5 0 0 0 2.9 6.3l1.4 1.6A5.3 5.3 0 0 1 8 6.4Z" /></svg>
                <svg viewBox="0 0 27 13" width="27" height="13"><rect x="0.5" y="0.5" width="23" height="12" rx="3.5" fill="none" stroke="currentColor" opacity="0.4" /><rect x="2.5" y="2.5" width="17" height="8" rx="2" /><rect x="24.8" y="4.5" width="1.6" height="4" rx="0.8" opacity="0.4" /></svg>
              </span>
            </div>
            <iframe key={`${src}#${run}`} src={src} title={t.frame} style={{ width: SCREEN.w, height: SCREEN.h - STATUS - HOME }} data-testid="phone-frame" />
            <div className="ph-home" style={{ height: HOME }} aria-hidden="true"><span /></div>
          </div>
        </div>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Phone />
  </StrictMode>,
);
