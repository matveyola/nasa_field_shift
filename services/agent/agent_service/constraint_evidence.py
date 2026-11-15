"""Does the quoted evidence support the proposed farmer constraint? (Task 05C, 2026-11-14)

The evidence must already be an exact span of the farmer's CURRENT message (tools.propose_constraints, service
_finalize). This module checks what a substring test cannot: that the span was not cut away from a negation or a
qualifier ("Не | маю поливу"), that it is not hedged ("мабуть є полив"), that its words carry the proposed meaning
(a negation for "no irrigation", a named crop for a crop list, the number for a rotation length), that a soft wish
is not filed as a hard exclusion, and that the same message does not also say the opposite.

These are deterministic heuristics over Ukrainian and English wording, not language understanding. They are tuned to
refuse with a reason the model can act on (quote more words, propose it as inferred, or ask the farmer) and to leave
ordinary statements alone; meaning the patterns cannot see stays with the model and the farmer's confirmation.
"""

import re

_I = re.IGNORECASE
_WORD = re.compile(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)*|\d+")
_SENTENCE_BREAK = re.compile(r"[.!?;\n]")
_CLAUSE_BREAK = re.compile(r"[.!?;:,\n]")
_CONTRAST = re.compile(
    r"(?<![^\W\d_])(?:але|проте|однак|хоча|зате|but|however|although|though|yet|whereas|except)"
    r"(?![^\W\d_])",
    _I,
)

_NEGATIONS = frozenset(
    {
        "не",
        "ні",
        "нема",
        "немає",
        "без",
        "ніколи",
        "жоден",
        "жодна",
        "жодного",
        "жодної",
        "жодних",
        "відсутній",
        "відсутня",
        "відсутнє",
        "відсутні",
        "no",
        "not",
        "never",
        "without",
        "none",
        "nor",
        "cannot",
        "dont",
        "cant",
        "wont",
    }
)
# Words right after a quote that change what it says: "полив є | лише на частині", "irrigation | only in spring".
_QUALIFIERS = _NEGATIONS | {
    "лише",
    "тільки",
    "частково",
    "іноді",
    "крім",
    "окрім",
    "only",
    "sometimes",
    "partly",
    "partially",
    "just",
    "except",
}
_HEDGES = re.compile(
    r"(?<![^\W\d_])(?:можливо|мабуть|напевно|здається|ніби|наче|думаю|гадаю|не\s+впевнен\w*|не\s+знаю"
    r"|maybe|perhaps|probably|possibly|might|not\s+sure|unsure|i\s+think|i\s+guess|i\s+believe)"
    r"(?![^\W\d_])",
    _I,
)
# A wish, not a rule: "краще не сіяти соняшник", "I'd rather avoid sunflower".
_SOFT = re.compile(
    r"краще|бажано|по\s+можливості|якщо\s+можна|намага\w*|хотілося\s+б|хотів\s+би|хотіла\s+б|уника\w*"
    r"|rather|prefer\w*|ideally|if\s+possible|would\s+like|try\s+to|avoid\w*",
    _I,
)
_EXCLUSION = re.compile(r"виключ\w*|заборон\w*|exclud\w*|\bban\w*", _I)
_PAST = re.compile(
    r"(?<![^\W\d_])(?:раніше|колись|був|була|було|були|мав|мала|мали|before|previously|used\s+to|had"
    r"|was|were|said)(?![^\W\d_])",
    _I,
)
# Words that make a statement about now or from now on, even next to a past one ("був, більше не сіятиму").
_PRESENT = re.compile(
    r"(?<![^\W\d_])(?:тепер|зараз|нині|відтепер|надалі|більше\s+не|now|currently|today|this\s+(?:year|season)"
    r"|цього\s+(?:року|сезону)|наступного\s+року|next\s+year|no\s+longer|anymore|any\s+more|from\s+now)"
    r"(?![^\W\d_])",
    _I,
)
_SCOPE = re.compile(
    r"(?<![^\W\d_])(?:пол[еяіюях]\w*|ділянк\w*|частин\w*|fields?|plots?|parts?|partly)(?![^\W\d_])",
    _I,
)

_TOPIC = {
    "irrigation_available": re.compile(
        r"полив\w*|зрош\w*|вод\w*|крапельн\w*|дощуван\w*|богар\w*|irrigat\w*|water\w*"
        r"|pivot\w*|sprinkler\w*|drip|dryland|rain-?fed",
        _I,
    ),
    "erosion_protection": re.compile(
        r"ероз\w*|схил\w*|змив\w*|вимив\w*|erosion|slope\w*|runoff|wash\w*", _I
    ),
    "organic_only": re.compile(
        r"органі\w*|біо\w*|хімі\w*|пестицид\w*|organic\w*|chemical\w*|pesticide\w*|synthetic",
        _I,
    ),
    "fertilizer_budget": re.compile(
        r"добрив\w*|азот\w*|селітр\w*|карбамід\w*|fertili\w*|nitrogen|urea|(?<!\w)N(?!\w)",
        _I,
    ),
    "rotation_length_years": re.compile(
        r"рок\w*|рік|літ|сівозмін\w*|years?|rotation\w*", _I
    ),
}
# Words that mean there is no irrigation without a negation word: "богара", "полив відключили", "unavailable".
_NO_WATER_WORDS = re.compile(
    r"богар\w*|dryland|rain-?fed|недоступн\w*|unavailable|відключ\w*|вимкн\w*|switched\s+off"
    r"|shut\s+off|cut\s+off|(?<![^\W\d_])lost(?![^\W\d_])",
    _I,
)
# A negation that is about chemicals, not about being organic: "без хімії", "no pesticides" mean organic.
_NO_CHEMICALS = re.compile(
    r"(?:без|no|without|не\s+використову\w*|не\s+застосову\w*)\s+(?:\w+\s+)?"
    r"(?:хімі\w*|пестицид\w*|гербіцид\w*|мінеральн\w*|chemical\w*|pesticide\w*|herbicide\w*|synthetic)",
    _I,
)
# "Not against" a crop is the opposite of ruling it out: "не маю нічого проти соняшника", "I don't mind sunflower".
_NOT_AGAINST = re.compile(
    r"(?:нічого|не)\s+проти|не\s+заперечу\w*|don['’]?t\s+mind|do\s+not\s+mind|no\s+objection"
    r"|not\s+against",
    _I,
)
BOOLEAN_TYPES = ("irrigation_available", "erosion_protection", "organic_only")

# How a farmer names the app's catalogue crops (plain.CROPS). Generic "пшениця" / "wheat" is the only wheat there.
CROP_NAMES = {
    "maize": r"кукурудз\w*|maize|corn",
    "sunflower": r"соняшник\w*|sunflowers?",
    "soybean": r"со[яюїє]\w*|soy\w*",
    "winter_wheat": r"пшениц\w*|wheat",
    "winter_pea": r"горох\w*|peas?",
    "winter_rye_vetch": r"жит\w*|вик[аиуоі]\w*|rye|vetch|покривн\w*|cover\s+crops?",
    "sorghum": r"сорго|sorghum",
}
_NUMBER_WORDS = {
    2: r"два|дві|двох|two",
    3: r"три|трьох|three",
    4: r"чотир\w*|four",
    5: r"п['’ʼ]?ять\w*|five",
    6: r"шіст\w*|шести|six",
}


def _tokens(text: str) -> list[str]:
    return [
        t.casefold().replace("’", "'").replace("ʼ", "'") for t in _WORD.findall(text)
    ]


def _negated(text: str) -> bool:
    return any(t in _NEGATIONS or t.endswith("n't") for t in _tokens(text))


def _named(crop_id: str, text: str) -> bool:
    pattern = CROP_NAMES.get(crop_id) or re.escape(crop_id.replace("_", " "))
    return bool(re.search(rf"(?<![^\W\d_])(?:{pattern})(?![^\W\d_])", text, _I))


def _clauses(text: str) -> list[str]:
    return [
        c
        for c in re.split(
            r"[.!?;\n]+|,?\s*(?<![^\W\d_])(?:але|проте|однак|хоча|зате|but|however|although"
            r"|though|yet|whereas)(?![^\W\d_])",
            text,
            flags=_I,
        )
        if c and c.strip()
    ]


def _cut(message: str, evidence: str) -> str | None:
    """Why some occurrence of `evidence` in the message is a fragment that drops meaning, or None."""
    for start in (i for i in range(len(message)) if message.startswith(evidence, i)):
        end = start + len(evidence)
        if (evidence[0].isalnum() and start and message[start - 1].isalnum()) or (
            evidence[-1].isalnum() and end < len(message) and message[end].isalnum()
        ):
            return "The quote starts or ends inside a word. Quote whole words."
        clause_before = _CLAUSE_BREAK.split(message[:start])[-1]
        if any(
            t in _NEGATIONS or t.endswith("n't") for t in _tokens(clause_before)[-3:]
        ):
            return (
                f'The farmer\'s words just before the quote ("{clause_before.strip()}") negate or qualify it. '
                "Quote the whole statement, including the negation, and set the value it means."
            )
        following = re.match(r"\s*([^\W\d_]+(?:['’ʼ][^\W\d_]+)*)", message[end:])
        if following and (
            following.group(1).casefold() in _QUALIFIERS
            or following.group(1).casefold().endswith("n't")
        ):
            return (
                f'The farmer\'s next word ("{following.group(1)}") qualifies the quote. Quote the whole statement '
                "including it, or ask the farmer what they mean."
            )
    return None


def _occurrence_context(message: str, evidence: str) -> list[tuple[str, str]]:
    """(sentence text before the quote, sentence text after it) for every occurrence."""
    out = []
    for start in (i for i in range(len(message)) if message.startswith(evidence, i)):
        end = start + len(evidence)
        out.append(
            (
                _SENTENCE_BREAK.split(message[:start])[-1],
                _SENTENCE_BREAK.split(message[end:])[0],
            )
        )
    return out


def problem(
    ctype: str,
    value: object,
    evidence: str,
    message: str,
    *,
    explicit: bool,
    known_crops: set[str],
) -> tuple[str, str] | None:
    """(category, message for the model) when the evidence cannot carry this constraint, else None.

    `evidence` is already known to occur in `message` (or to be empty for an inference without a quote)."""
    crops = (
        [value]
        if ctype == "previous_crop"
        else value
        if ctype in ("excluded_crops", "preferred_crops", "market_preference")
        else []
    )
    if isinstance(crops, list) and (
        unknown := [c for c in crops if isinstance(c, str) and c not in known_crops]
    ):
        return (
            "unsupported_crop",
            (
                f"{', '.join(unknown)} is not in the app's crop list ({', '.join(sorted(known_crops))}). "
                "Tell the farmer this crop is not covered; never substitute another crop."
            ),
        )
    if not evidence:
        return None
    if cut := _cut(message, evidence):
        return "evidence_cut", cut
    if not explicit:
        return None

    contexts = _occurrence_context(message, evidence)
    if _HEDGES.search(evidence) or any(
        _HEDGES.search(before) for before, _ in contexts
    ):
        return (
            "uncertain_statement",
            (
                'The farmer sounds unsure here. Propose it with basis "inferred" and a '
                "confidence for them to confirm, or ask."
            ),
        )
    if (
        ctype != "previous_crop"
        and _PAST.search(evidence)
        and not _PRESENT.search(evidence)
    ):  # a previous crop is past
        return (
            "past_statement",
            (
                "The quote describes the past. Propose only what holds now, quoting the farmer's "
                "words about now, or ask."
            ),
        )
    topic = _TOPIC.get(ctype)
    for _, after in contexts:
        rest = after
        if (
            _CONTRAST.search(rest)
            and (_negated(rest) or set(_tokens(rest)) & _QUALIFIERS)
            and (
                (topic is not None and topic.search(rest))
                or _SCOPE.search(rest)
                or any(_named(c, rest) for c in crops if isinstance(c, str))
            )
        ):
            return (
                "condition_omitted",
                (
                    f'The farmer qualified this in the same sentence ("{rest.strip()[:80]}"). '
                    "Quote the qualified statement and set the value it means for this field, or ask."
                ),
            )

    if topic is not None and not topic.search(evidence):
        return (
            "evidence_too_short",
            (
                "The quote does not name what the constraint is about. Quote the farmer's words "
                'that name it (for example "немає поливу", not "немає").'
            ),
        )
    for crop in crops:
        if isinstance(crop, str) and not _named(crop, evidence):
            return (
                "crop_not_mentioned",
                (
                    f"The quote does not name {crop}. Quote the words where the farmer names it; "
                    "never add a crop the farmer did not name."
                ),
            )
    negated = _negated(evidence)
    not_against = bool(_NOT_AGAINST.search(evidence))
    if ctype in BOOLEAN_TYPES:
        no_water = ctype == "irrigation_available" and bool(
            _NO_WATER_WORDS.search(evidence)
        )
        # Erosion protection is often asked for with a negation ("не допустити ерозії"): no polarity rule for it.
        # Organic farming is often stated as a negated chemical ("без хімії").
        refuses = (
            (negated or no_water)
            and ctype != "erosion_protection"
            and not (ctype == "organic_only" and _NO_CHEMICALS.search(evidence))
        )
        if value is True and refuses:
            return (
                "polarity_mismatch",
                (
                    "The quote is negated, so it cannot mean true. Check the value, or quote the "
                    "affirmative statement if there is one."
                ),
            )
        if (
            ctype == "irrigation_available"
            and value is False
            and not negated
            and not no_water
        ):
            return (
                "polarity_mismatch",
                "Nothing in the quote says there is no irrigation. Quote the negation, or ask.",
            )
        if ctype == "irrigation_available" and (
            opposite := _opposite_irrigation(message, evidence, bool(value))
        ):
            return (
                "conflict",
                (
                    f'The same message also says "{opposite}". Ask the farmer which holds '
                    "for this field instead of choosing."
                ),
            )
    if (
        ctype == "rotation_length_years"
        and isinstance(value, int)
        and not re.search(
            rf"(?<!\d){value}(?!\d)|(?<![^\W\d_])(?:{_NUMBER_WORDS.get(value, '$^')})",
            evidence,
            _I,
        )
    ):
        return (
            "value_not_in_evidence",
            f"The quote does not contain {value}. Quote the farmer's number.",
        )
    if ctype == "excluded_crops":
        if not_against:
            return (
                "polarity_mismatch",
                "The farmer says they are not against this crop, so it is not excluded.",
            )
        if _SOFT.search(evidence):
            return (
                "preference_not_restriction",
                (
                    'This is a wish, not a rule. Propose it with basis "inferred" and a '
                    "confidence, or ask whether the crop must be excluded."
                ),
            )
        if not negated and not _EXCLUSION.search(evidence):
            return (
                "polarity_mismatch",
                "The quote does not say the crop is ruled out. Quote the farmer's refusal, or ask.",
            )
    if ctype in ("preferred_crops", "market_preference") and not_against:
        return (
            "uncertain_statement",
            (
                'Not minding a crop is not a preference. Propose it with basis "inferred" and '
                "a confidence, or ask."
            ),
        )
    if ctype in ("preferred_crops", "market_preference") and negated:
        return (
            "polarity_mismatch",
            (
                "The quote is negated, so it is not a preference. Check whether the farmer meant "
                "to exclude the crop."
            ),
        )
    return None


def _opposite_irrigation(message: str, evidence: str, value: bool) -> str | None:
    """Another current-tense clause about water that says the opposite. A clause about the past ("раніше був полив")
    is not a contradiction, and a quote scoped to this field ("на це поле воду не подаватиму") is the more specific
    statement, so a general one elsewhere does not contradict it."""
    if _SCOPE.search(evidence):
        return None
    for clause in _clauses(message):
        if (
            evidence in clause
            or clause.strip() in evidence
            or not _TOPIC["irrigation_available"].search(clause)
        ):
            continue
        if _PAST.search(clause) or _HEDGES.search(
            clause
        ):  # "є полив на частині поля" still contradicts "поливу немає"
            continue
        says = not (_negated(clause) or _NO_WATER_WORDS.search(clause))
        if says != value:
            return clause.strip()[:80]
    return None


def crop_catalogue(field_crops: list[str] | tuple[str, ...] = ()) -> set[str]:
    """The crop ids a constraint may name: the app's catalogue plus crops already in this field's history."""
    from .plain import CROPS

    return set(CROPS["en"]) | set(field_crops)
