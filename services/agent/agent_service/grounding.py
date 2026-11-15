"""Grounding of free text: fact references, trusted rendering, and claim checks.

The model never writes a field measurement itself. It writes `{{fact:<id>}}`; the server
renders the value, unit, uncertainty and, where needed, a short origin note from the verified fact. A checker then
looks for numbers and claims the model wrote on its own.

What this does NOT guarantee (see README › Grounding): numbers written as words, qualitative
claims ("the soil is dry"), and paraphrased claims outside the patterns below are not caught.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from contracts.models import CALC_FACT_PREFIX, Fact, Provenance, Quantity

from . import grounding_dates, plain, scenarios

# ------------------------------------------------------------------ formatting (shared with the dev agent)
# What the text says after a value, in plain words (see plain.py). Plain data from a satellite or a weather service
# gets no note: its source and period are in the "data used" panel. A note is kept only where the reader could take
# the number for something it is not: an estimate, a calculation, a rotation result, the farmer's own words, a value
# the data service did not confirm, data saved earlier, and demo data when the same text also gives real values
# (otherwise one notice under the answer says the numbers are demo data; the owner's call, 2026-11-12).

LABELS = {
    "uk": {
        "demo": "демо",
        "cached": "збережено раніше",
        "unavailable": "немає даних",
        "estimate": "оцінка",
        "estimate_unchecked": "неперевірена оцінка",
        "calc": "розрахунок",
        "reference": "довідкове значення",
        "farmer": "з ваших слів",
        "simulation": "розрахунок сівозміни, варіант «{scenario}»",
        "arithmetic": "розрахунок з ваших чисел",
        "unverified": "не підтверджено сервісом даних",
    },
    "en": {
        "demo": "demo",
        "cached": "saved earlier",
        "unavailable": "no data",
        "estimate": "estimate",
        "estimate_unchecked": "unchecked estimate",
        "calc": "calculated",
        "reference": "reference value",
        "farmer": "from you",
        "simulation": "rotation calculation, case “{scenario}”",
        "arithmetic": "calculated from your figures",
        "unverified": "not confirmed by the data service",
    },
}
_SCENARIO_IN_METHOD = re.compile(r"scenario=([a-z0-9_]+)")


def format_number(value: float, lang: str) -> str:
    text = f"{value:g}"
    return text.replace(".", ",") if lang == "uk" else text


MONEY_UNITS = frozenset({"UAH", "USD", "UAH/ha", "USD/ha"})


def format_money(value: float, lang: str) -> str:
    """Grouped thousands and kopecks/cents only when there are any: "7 500", "22 500,50" (uk); "7,500", "22,500.50"."""
    text = f"{value:,.2f}".removesuffix(".00")
    return (
        text.replace(",", "\u00a0").replace(".", ",") if lang == "uk" else text
    )  # no-break space, as Intl uk-UA


def format_quantity(q: Quantity, lang: str) -> str:
    number = (
        format_money(q.value, lang)
        if q.unit in MONEY_UNITS
        else format_number(q.value, lang)
    )
    text = number + plain.unit(q.unit, lang, q.value)
    u = q.uncertainty
    if u and u.kind in ("range", "ci95") and u.low is not None and u.high is not None:
        text += f" ({format_number(u.low, lang)}–{format_number(u.high, lang)})"
    elif u and u.kind == "sd" and u.sd is not None:
        text += f" ± {format_number(u.sd, lang)}"
    return text


def product_name(p: Provenance) -> str:
    if not p.source:
        return ""
    version = p.source.version
    return f"{p.source.product_id} " + (
        f"v{version}" if version != "unverified" else "(version unverified)"
    )


def origin_note(p: Provenance, lang: str, mark_demo: bool = True) -> str:
    """The short note after a value in the text, or "" for plain data (see LABELS above). `mark_demo` is False when
    every value in the text is demo data: the answer's own notice says so once."""
    t = LABELS[lang]
    parts = []
    if p.kind == "ml_estimate":
        parts.append(
            t["estimate"] if p.model and p.model.validation else t["estimate_unchecked"]
        )
    elif p.kind == "deterministic_calc":
        parts.append(t["calc"])
    elif p.kind == "reference":
        source = plain.source(p.source.product_id if p.source else None, lang)
        parts.append(f"{t['reference']}: {source}" if source else t["reference"])
    elif p.kind == "farmer_input":
        parts.append(t["farmer"])
    if p.data_status == "fixture" and mark_demo:
        parts.append(t["demo"])
    elif p.data_status == "cached":
        parts.append(t["cached"])
    return ", ".join(parts)


def is_farmer_arithmetic(f: Fact) -> bool:
    """An input or result of calculate_farm_budget: the farmer's own figures, never field data or live data."""
    return f.domain == "farm" and f.id.startswith(
        CALC_FACT_PREFIX
    )  # a FieldContext cannot carry such ids


def is_simulation_output(f: Fact) -> bool:
    """A score from a compare_rotations result (see integrations.normalize.check_comparison)."""
    return f.domain == "rotation" and f.id.startswith("sim.")


def render_fact(
    f: Fact, lang: str, unverified: bool = False, mark_demo: bool = True
) -> str:
    if f.provenance.data_status == "unavailable":
        name = plain.source(
            f.provenance.source.product_id if f.provenance.source else None, lang
        )
        return f"{LABELS[lang]['unavailable']}" + (f" ({name})" if name else "")
    value = (
        format_quantity(f.quantity, lang)
        if f.quantity is not None
        else (f.category or "")
    )
    note = origin_note(f.provenance, lang, mark_demo)
    if is_farmer_arithmetic(f) and f.provenance.kind == "deterministic_calc":
        note = LABELS[lang]["arithmetic"]
    if is_simulation_output(f):
        m = _SCENARIO_IN_METHOD.search(f.provenance.method or "")
        # display name only; the id stays in provenance.method and every contract (unknown id → shown as is)
        if (
            f.provenance.kind == "deterministic_calc"
        ):  # the simulation label already says it is a calculation
            note = note.removeprefix(LABELS[lang]["calc"]).removeprefix(", ")
        note = ", ".join(
            x
            for x in (
                LABELS[lang]["simulation"].format(
                    scenario=scenarios.label(m.group(1), lang) if m else "?"
                ),
                note,
            )
            if x
        )
    if unverified and f.provenance.data_status in ("live", "cached"):
        note = ", ".join(x for x in (note, LABELS[lang]["unverified"]) if x)
    return f"**{value}**" + (f" ({note})" if note else "")


def label_problem(text: str, simulation_ran: bool = False) -> str | None:
    """Why a short model-written label (a block title, row or column name) cannot be shown, or None.
    Labels are words only: values are fact references the app renders with unit and origin."""
    for m in _NUMBER.finditer(text):
        if not _YEAR.fullmatch(m.group(0).lstrip("+-±")):
            return "no numbers in labels: show values only as fact ids"
    for category, pattern in _CLAIMS.items():
        if category == "simulation_claim" and simulation_ran:
            continue
        if pattern.search(text):
            return f"unsupported claim ({category})"
    return None


def paragraph_chunks(markdown: str) -> list[str]:
    """Split into paragraphs that concatenate back to exactly `markdown`."""
    return [c for c in re.split(r"(?<=\n\n)", markdown) if c]


# ------------------------------------------------------------------ checks

FACT_TOKEN = re.compile(r"\{\{\s*fact\s*:\s*([^{}\s]{1,128})\s*\}\}")

# A number not glued to a preceding letter (L4, V008) or letter-hyphen (FAO-56, poltava-01).
_NUMBER = re.compile(r"(?<![^\W\d_])(?<![^\W\d_]-)(?<![\d.,])[-+±]?\d+(?:[.,]\d+)*")
_YEAR = re.compile(r"(?:19|20)\d{2}")
# Units after a number turn it into a measurement claim, whatever its size.
_UNIT_AFTER = re.compile(
    r"\s?(?:%|‰|°|℃|mm\b|мм\b|cm\b|см\b|km\b|км\b|m\b|м\b|m[²³23]|м[²³23]|ha\b|га\b|kg\b|кг\b|t\b|т\b|ц\b"
    r"|g/kg|г/кг|mg|мг|cmol|MJ|W/m|t/ha|т/га|ц/га|kg/ha|кг/га|mm/d|K\b|percent|відсот|процент"
    # money and its scale words: a price or cost is a claim whatever its size
    r"|грн|гривн|гривень|₴|UAH\b|USD\b|\$|долар|dollars?\b|тис\.|тисяч|млн|thousand|million)",
    re.IGNORECASE,
)
_PH_BEFORE = re.compile(r"\bpH\W{0,3}$", re.IGNORECASE)

# Prose claims the M2 system cannot support. Patterns are heuristics with known gaps.
_CLAIMS: dict[str, re.Pattern[str]] = {
    "simulation_claim": re.compile(
        r"симуляці\w*\s+(?:показал|засвідчил|підтвердил|розрахувал|передбачил)|результат\w*\s+(?:симуляці|моделюванн)"
        r"|(?:я|ми)\s+(?:запустив|запустила|провів|провела|виконав|виконала)\w*\s+(?:симуляці|моделюванн)"
        r"|simulation\s+(?:shows?|showed|results?|indicates?|predicts?|found|confirms?)|simulated\s+(?:yield|result)"
        r"|\b(?:I|we)\s+(?:ran|performed|completed|ran\s+a)\s+(?:a\s+|the\s+)?simulation",
        re.IGNORECASE,
    ),
    "live_claim": re.compile(
        # Bare "right now" / "прямо зараз" are ordinary phrases ("not available right now"): not included.
        r"\breal[- ]time\b|\blive\s+(?:data|reading|measurement|satellite)|currently\s+measur"
        r"|у\s+реальному\s+часі|щойно\s+виміря|поточн\w+\s+вимірюван",
        re.IGNORECASE,
    ),
    # No simulation or optimisation exists: no rotation can be called optimal or best.
    "unsupported_recommendation": re.compile(
        r"(?:оптимальн|найкращ|найвигідніш)\w*\s+(?:\w+\s+){0,2}сівозмін|сівозмін\w*\s+(?:\w+\s+){0,2}(?:оптимальн|найкращ)"
        r"|\b(?:optimal|best|ideal)\s+(?:\w+\s+){0,2}rotation",
        re.IGNORECASE,
    ),
    # No connected simulation service applies the farmer's confirmed conditions (tools.compare_rotations): a result
    # must never be said to reflect them. The negated forms ("не враховує ваших умов") are what the agent should say.
    "constraints_applied_claim": re.compile(
        r"(?<!не )(?<!не\s)\bвраховує\w*\s+(?:\w+\s+){0,2}(?:ваш\w*|фермер\w*)\s+(?:\w+\s+){0,2}(?:умов|обмежен|вимог)\w*"
        r"|(?<!без )\bз\s+урахуванням\s+(?:\w+\s+){0,1}ваш\w*\s+(?:\w+\s+){0,2}(?:умов|обмежен|вимог)\w*"
        r"|(?<!not )(?<!n't )\b(?:takes?|took|taking)\s+(?:\w+\s+){0,2}into\s+account\s+your\s+(?:\w+\s+){0,2}"
        r"(?:conditions|constraints|restrictions|rules)"
        r"|(?<!not )(?<!n't )\b(?:reflects?|follows?|respects?)\s+your\s+(?:\w+\s+){0,2}"
        r"(?:conditions|constraints|restrictions|rules)",
        re.IGNORECASE,
    ),
    # estimate_as_observation is checked per sentence in check(): it needs the cited facts' provenance.
    "photo_lab_claim": re.compile(
        r"(?:з|за|по)\s+фото\w*.{0,60}(?:pH|кислотн|азот|фосфор|калі|гумус)"
        r"|(?:photo|image|picture).{0,60}(?:pH|acidity|nitrogen|phosphorus|potassium|organic\s+matter)",
        re.IGNORECASE,
    ),
}


# Wording that turns a computed or estimated value into an observation. Negated forms ("not measured",
# "не виміряно") are allowed: that is exactly what the agent should say.
_OBSERVED = re.compile(
    r"(?<!not )(?<!never )\b(?:measured|observed|recorded|actual\s+(?:yield|harvest))\b"
    r"|(?<!не )(?<!не\s)\b(?:виміря\w*|спостерігал\w*|зафіксова\w*|фактичн\w+\s+(?:урожа|врожа)\w*)",
    re.IGNORECASE,
)


# Demo data called real. Checked only when every value the turn can see is demo data (see check(), demo_only); the
# negated forms ("не справжній вимір", "not real data") are what the agent should say and are allowed.
_DEMO_AS_REAL = re.compile(
    r"(?<!не )(?<!не\s)\b(?:фактичн|реальн|справжн)\w*\s+(?:\w+\s+){0,2}?(?:спостереж|дан\w|вимір|показник)\w*"
    r"|(?<!not )\b(?:actual|real|genuine)\s+(?:\w+\s+){0,2}?(?:observations?|data|measurements?|readings?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    category: (
        str  # unknown_fact | fact_not_retrieved | ungrounded_number | <claim category>
    )
    snippet: str  # short, for the repair instruction and logs (never the whole answer)
    start: int  # position in the model's raw text
    end: int


@dataclass
class Grounded:
    markdown: str
    fact_ids: list[str] = field(
        default_factory=list
    )  # facts referenced, in order of first use
    violations: list[Violation] = field(default_factory=list)
    citations: list = field(
        default_factory=list
    )  # contracts.models.Citation, numbered as in markdown


def _number_is_claim(text: str, m: re.Match[str], farmer_numbers: set[str]) -> bool:
    raw = m.group(0).lstrip("+-±")
    after = text[m.end() : m.end() + 12]
    has_unit = bool(_UNIT_AFTER.match(after))
    if _PH_BEFORE.search(text[max(0, m.start() - 6) : m.start()]):
        return True
    if raw in farmer_numbers and not ("." in raw or "," in raw):
        return False  # echoing the farmer's own number ("my 50 ha")
    if _YEAR.fullmatch(raw) and not has_unit:
        return False  # a year
    if has_unit or "." in raw or "," in raw:
        return True  # a measurement or a precise value
    return int(raw) > 10  # small integers: counts, list items, "3 years"


def visible_text(text: str) -> str:
    """The model's text without invisible format characters (Unicode Cf: zero-width spaces and joiners, bidi
    controls, soft hyphens). "2<ZWSP>5<ZWSP>0" reads as 250 on screen but slipped past the number checks as three
    small digits. Letters, apostrophes (ʼ ’) and no-break spaces are not Cf and stay; source passages are never
    touched (only the model-written draft goes through here)."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _ground_dates(text, masked, retrieved, unverified):
    """Mask full date spans only; unsupported spans remain explicit violations.

    A sentence naming a fact cannot borrow dates from another fact, source
    retrieval time, model version, field name, farmer text or old chat. Without
    a fact token, only current retrieved temporal context can support a date.
    """
    violations = []
    spans = grounding_dates.date_spans(masked)
    sentences = list(_SENTENCE.finditer(text))
    resolved = []
    scoped_periods = []
    for i, span in enumerate(spans):
        sentence = next(s for s in sentences if s.start() <= span.start < s.end())
        refs = list(FACT_TOKEN.finditer(text, sentence.start(), sentence.end()))
        # A range describes one period. Resolve both ends against the same
        # adjacent reference, not against the union of all dates mentioned in
        # a comparison sentence (which would allow swapped days).
        first, last = i, i
        while first and grounding_dates.joins_range(
            masked, spans[first - 1], spans[first]
        ):
            first -= 1
        while last + 1 < len(spans) and grounding_dates.joins_range(
            masked, spans[last], spans[last + 1]
        ):
            last += 1
        start, end = spans[first].start, spans[last].end
        nearby = []
        for ref in refs:
            gap = (
                text[ref.end() : start]
                if ref.end() <= start
                else text[end : ref.start()]
            )
            if ";" not in gap and "\n" not in gap:
                nearby.append((len(gap), ref.end() <= start, ref.start(), ref.group(1)))
        ids = [min(nearby)[3]] if nearby else []
        candidates = (
            [retrieved[fid] for fid in ids if fid in retrieved]
            if refs
            else retrieved.values()
        )
        periods = {
            (f.provenance.temporal.start, f.provenance.temporal.end)
            for f in candidates
            if f.id not in unverified
            and f.provenance.temporal is not None
            and f.provenance.data_status != "unavailable"
            and f.provenance.kind != "farmer_input"
        }
        endpoints = {d for period in periods for d in period}
        value = span.resolve(endpoints, grounding_dates.range_year(masked, spans, i))
        # A date immediately used as money/a measurement is not a date escape.
        if _UNIT_AFTER.match(masked[span.end : span.end + 12]):
            value = None
        resolved.append(value)
        scoped_periods.append(periods)
    invalid = {i for i, value in enumerate(resolved) if value is None}
    for i in range(len(spans) - 1):
        if grounding_dates.joins_range(masked, spans[i], spans[i + 1]):
            pair = (resolved[i], resolved[i + 1])
            if pair not in scoped_periods[i] or pair not in scoped_periods[i + 1]:
                invalid.update((i, i + 1))
    for i, span in enumerate(spans):
        if i in invalid:
            violations.append(
                Violation(
                    "ungrounded_date", text[span.start : span.end], span.start, span.end
                )
            )
    # Rejected dates are already violations; do not also diagnose their digits
    # as measurements. Nothing grants those digits authority elsewhere.
    for span in reversed(spans):
        masked = (
            masked[: span.start] + "§" * (span.end - span.start) + masked[span.end :]
        )
    return masked, violations


def _bare_fact_context(fact, retrieved, lang, unverified=False):
    """Keep a standalone temporal value identifiable after sentence redaction."""
    temporal = fact.provenance.temporal
    if fact.provenance.data_status == "unavailable":
        return ""
    # Unknown labels are not inferred from ids. Existing translated vocabulary
    # is the authority; provenance dates/sources remain useful independently.
    parts = [plain.NAMES[lang].get(fact.label_key, "")]
    if unverified or fact.provenance.kind == "farmer_input":
        return parts[0]  # Do not reinsert dates rejected by the trust boundary.
    if temporal is not None:
        parts.append(plain.period(temporal.start, temporal.end, lang))
    elif fact.domain in {"climate", "water"}:
        parts.append("період не вказано" if lang == "uk" else "period not supplied")
    sources = [fact.provenance.source]
    if fact.provenance.kind == "deterministic_calc":
        sources += [
            retrieved[fid].provenance.source
            for fid in fact.provenance.inputs
            if fid in retrieved and retrieved[fid].provenance.temporal == temporal
        ]
    parts += list(dict.fromkeys(plain.source(s.product_id, lang) for s in sources if s))
    return "; ".join(p for p in parts if p)


def check(
    text: str,
    *,
    retrieved: dict[str, Fact],
    known_ids: set[str],
    farmer_text: str,
    simulation_ran: bool,
    any_live_fact: bool,
    lang: str,
    evidence: dict | None = None,
    index_has=None,
    unverified: frozenset[str] | set[str] = frozenset(),
    demo_only: bool = False,
) -> Grounded:
    """Render fact and citation tokens and report every grounding violation in `text`.

    `evidence` is this turn's retrieved knowledge chunks (chunk_id -> ChunkRecord); `index_has`
    tells whether a chunk id exists in the index at all (fabricated vs. not retrieved).
    """
    from . import (
        citations,  # local import: grounding stays importable without the knowledge package
    )

    violations: list[Violation] = []
    used: list[str] = []
    evidence = evidence or {}
    cite_issues, cited_sentences = citations.resolve(
        text, evidence, index_has or (lambda _cid: False)
    )
    violations += [
        Violation(i.category, i.snippet, i.start, i.end) for i in cite_issues
    ]

    # 1. Fact references.
    for m in FACT_TOKEN.finditer(text):
        fid = m.group(1)
        if fid not in known_ids:
            violations.append(
                Violation("unknown_fact", m.group(0)[:60], m.start(), m.end())
            )
        elif fid not in retrieved:
            violations.append(
                Violation("fact_not_retrieved", m.group(0)[:60], m.start(), m.end())
            )
        elif fid not in used:
            used.append(fid)

    # 2. Numbers the model wrote itself (tokens, verified dates and inline code are masked first). A claim
    #    number is allowed only inside a sentence citing a passage that contains it (row/column-aware).
    masked = FACT_TOKEN.sub(lambda m: "§" * len(m.group(0)), text)
    masked = citations.CITE_TOKEN.sub(lambda m: "§" * len(m.group(0)), masked)
    masked, date_violations = _ground_dates(text, masked, retrieved, unverified)
    violations += date_violations
    masked = scenarios.NAMES.sub(
        lambda m: "§" * len(m.group(0)), masked
    )  # "спека +2,5 °C" is a name, not a value
    masked = re.sub(r"`[^`\n]{0,80}`", lambda m: "§" * len(m.group(0)), masked)
    # A farmer's date is not a monetary or measurement input. Its component
    # digits must not become an allowance for unrelated numeric claims.
    farmer_scalars = grounding_dates.DATE.sub(" ", farmer_text)
    farmer_numbers = set(re.findall(r"\d+", farmer_scalars))
    sentence_has_claims: dict[int, bool] = {}
    for m in _NUMBER.finditer(masked):
        if not _number_is_claim(masked, m, farmer_numbers):
            continue
        cited = next((s for s in cited_sentences if s.start <= m.start() < s.end), None)
        snippet = masked[max(0, m.start() - 15) : m.end() + 10].strip()
        if cited is None:
            violations.append(
                Violation("ungrounded_number", snippet, m.start(), m.end())
            )
            continue
        sentence_has_claims[cited.start] = True
        if problem := citations.number_problem(
            cited.text, m.group(0).lstrip("+-±"), cited.chunks
        ):
            violations.append(Violation(problem, snippet, cited.start, cited.end))
    for sentence in cited_sentences:
        if problem := citations.sentence_problem(
            sentence, sentence_has_claims.get(sentence.start, False)
        ):
            violations.append(
                Violation(
                    problem, sentence.text.strip()[:60], sentence.start, sentence.end
                )
            )

    # 3. A computed value (model estimate, calculation, simulation output) described as observed.
    for sentence in _SENTENCE.finditer(text):
        cited_facts = [
            retrieved[m.group(1)]
            for m in FACT_TOKEN.finditer(sentence.group(0))
            if m.group(1) in retrieved
        ]
        computed = [
            f
            for f in cited_facts
            if f.provenance.kind in ("ml_estimate", "deterministic_calc")
            or is_simulation_output(f)
        ]
        if computed and (m := _OBSERVED.search(FACT_TOKEN.sub(" ", sentence.group(0)))):
            violations.append(
                Violation(
                    "estimate_as_observation",
                    m.group(0)[:60],
                    sentence.start(),
                    sentence.end(),
                )
            )

    # 3b. A simulation value presented under another scenario's name (M6C): a paragraph citing results of
    #     scenario A may name A (and others, e.g. "drought is not available; below: typical"), never only B.
    for para in _PARAGRAPH.finditer(text):
        cited = [
            retrieved[m.group(1)]
            for m in FACT_TOKEN.finditer(para.group(0))
            if m.group(1) in retrieved
        ]
        own = {
            m.group(1)
            for f in cited
            if is_simulation_output(f)
            and (m := _SCENARIO_IN_METHOD.search(f.provenance.method or ""))
        }
        named = scenarios.mentioned(FACT_TOKEN.sub(" ", para.group(0)))
        if own and named and not named & own:
            violations.append(
                Violation(
                    "scenario_mismatch",
                    para.group(0).strip()[:60],
                    para.start(),
                    para.end(),
                )
            )

    # 3c. Demo data called actual or real: when everything this turn can show is demo data, no wording may say otherwise.
    if demo_only:
        for m in _DEMO_AS_REAL.finditer(FACT_TOKEN.sub(" ", text)):
            violations.append(
                Violation("demo_as_real", m.group(0)[:60], m.start(), m.end())
            )

    # 4. Claims the system cannot back.
    for category, pattern in _CLAIMS.items():
        if category == "simulation_claim" and simulation_ran:
            continue
        if category == "live_claim" and any_live_fact:
            continue
        for m in pattern.finditer(text):
            violations.append(Violation(category, m.group(0)[:60], m.start(), m.end()))

    # Demo values are marked one by one only when the text also gives real ones (see LABELS).
    shown = [retrieved[fid].provenance.data_status for fid in used]
    mixed = "fixture" in shown and any(
        s not in ("fixture", "unavailable") for s in shown
    )
    bare_spans = [
        (s.start(), s.end())
        for s in _SENTENCE.finditer(text)
        if not FACT_TOKEN.sub("", s.group(0)).strip(" \t\n.,:;–—-()")
    ]

    def render(m):
        fid = m.group(1)
        if fid not in retrieved:
            return ""
        fact = retrieved[fid]
        value = render_fact(fact, lang, fid in unverified, mark_demo=mixed)
        if any(start <= m.start() < end for start, end in bare_spans):
            context = _bare_fact_context(fact, retrieved, lang, fid in unverified)
            if context:
                return f"{context}: {value}"
        return value

    rendered = FACT_TOKEN.sub(render, text)

    # 5. Citation tokens -> [n]; one Citation per chunk, quote = exact span closest to the claim.
    numbers: dict[str, int] = {}
    cites: list = []
    claim_of = {id(c): s.text for s in cited_sentences for c in s.chunks}

    def cite(m: re.Match[str]) -> str:
        cid, quote = m.group(1), m.group(2)
        chunk = evidence.get(cid)
        if chunk is None or (
            quote and citations._norm_ws(quote) not in citations._norm_ws(chunk.text)
        ):
            return ""  # invalid: reported as a violation above, never rendered
        if cid not in numbers:
            numbers[cid] = len(numbers) + 1
            exact = quote and _exact_span(chunk.text, quote)
            cites.append(
                citations.to_citation(
                    numbers[cid],
                    chunk,
                    exact or citations.best_quote(chunk, claim_of.get(id(chunk), "")),
                )
            )
        return f"[{numbers[cid]}]"

    rendered = citations.CITE_TOKEN.sub(cite, rendered)
    rendered = re.sub(r"[ \t]+(\[\d+\])", r" \1", rendered)
    return Grounded(
        markdown=rendered, fact_ids=used, violations=violations, citations=cites
    )


def _exact_span(text: str, quote: str) -> str | None:
    """The source's own characters for a whitespace-normalised quote (citations quote the source, not the model)."""
    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(0)[:300] if m else None


_PARAGRAPH = re.compile(r"(?:[^\n]|\n(?![ \t]*\n))+")
_SENTENCE = re.compile(
    r"(?:[^.!?\n]|[.!?](?![\s]|$))*(?:[.!?]+(?=\s|$)|\n|$)"
)  # a '.' ends a sentence only before whitespace: '1.20' does not


def redact(text: str, violations: list[Violation]) -> str:
    """Drop every sentence (or list line) of the raw text that contains a violation."""
    keep = []
    for m in _SENTENCE.finditer(text):
        if not m.group(0):
            continue
        if any(v.start < m.end() and v.end > m.start() for v in violations):
            if m.group(0).endswith("\n"):
                keep.append("\n")
            continue
        keep.append(m.group(0))
    cleaned = re.sub(r"\n{3,}", "\n\n", "".join(keep)).strip()
    return cleaned
