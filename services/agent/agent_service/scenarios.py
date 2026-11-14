"""Which simulation scenario a text asks for or talks about (M6C scenario integrity).

Invariant: a result for scenario A is never presented as the answer to a request for scenario B. These
patterns let the server check that without trusting the model: the farmer's message names the scenarios
that may be run, and a sentence citing a simulation value must not name only a different scenario.
Patterns are heuristics (uk/en); a text naming no scenario constrains nothing.
"""

import re

_DRY = r"посух|засух|посушлив|найсухіш|\bdrought|\bdry\s+(?:year|season|summer)|\bdriest"
_HOT = r"спек|спекотн|потеплін|\bheat|\bhottest|\bhot\s+(?:year|season|summer)"
WORDS: dict[str, re.Pattern[str]] = {
    "drought_2024": re.compile(_DRY, re.IGNORECASE),
    "heat_plus_2_5c": re.compile(_HOT + r"|warming|\+\s*2[.,]5", re.IGNORECASE),
    # A service that replays its own recent archive answers "drought" / "heat" with its driest / hottest season.
    "driest_recent_year": re.compile(_DRY, re.IGNORECASE),
    "hottest_recent_year": re.compile(_HOT, re.IGNORECASE),
    "typical": re.compile(r"типов|звичайн\w*\s+(?:умов|рік|року)|\btypical|\bnormal\s+(?:year|conditions)", re.IGNORECASE),
}
LABELS = {
    "uk": {"typical": "типові умови", "drought_2024": "посуха (як у 2024)", "heat_plus_2_5c": "спека +2,5 °C",
           "driest_recent_year": "найсухіший із недавніх років", "hottest_recent_year": "найспекотніший із недавніх років"},
    "en": {"typical": "typical conditions", "drought_2024": "drought (like 2024)", "heat_plus_2_5c": "heat +2.5 °C",
           "driest_recent_year": "driest recent year", "hottest_recent_year": "hottest recent year"},
}

# The exact display names, so the grounding check can tell a scenario's name ("спека +2,5 °C") from a number
# the model wrote itself. Only these server strings are exempt, verbatim.
NAMES = re.compile("|".join(re.escape(n) for n in sorted({n for d in LABELS.values() for n in d.values()}, key=len, reverse=True)))


# Scenarios that answer the same farmer words: a dry-year question is answered by whichever dry scenario the service
# has, and is not "unavailable" because the other one is missing.
FAMILY = {"drought_2024": "dry", "driest_recent_year": "dry", "heat_plus_2_5c": "hot", "hottest_recent_year": "hot"}


def family(scenario: str) -> str:
    return FAMILY.get(scenario, scenario)


def mentioned(text: str) -> set[str]:
    return {sid for sid, pattern in WORDS.items() if pattern.search(text or "")}


def label(scenario: str, lang: str) -> str:
    return LABELS.get(lang, LABELS["en"]).get(scenario, scenario)


def offer_text(scenario: str, lang: str) -> str:
    """The reply a farmer sends to choose another scenario; it names that scenario, so the tool accepts it."""
    return (f"Порівняй сівозміни для варіанту: {label(scenario, 'uk')}." if lang == "uk"
            else f"Compare rotations for the case: {label(scenario, 'en')}.")


if __name__ == "__main__":
    assert mentioned("Порівняй дві сівозміни під час посухи.") == {"drought_2024", "driest_recent_year"}
    assert mentioned(offer_text("typical", "uk")) == {"typical"} and mentioned(offer_text("typical", "en")) == {"typical"}
    assert mentioned("What is known about my field?") == set()
    assert mentioned("Compare drought and typical years") == {"drought_2024", "driest_recent_year", "typical"}
    assert mentioned(offer_text("hottest_recent_year", "uk")) == {"heat_plus_2_5c", "hottest_recent_year"}
    assert NAMES.search("умови «спека +2,5 °C»").group(0) == "спека +2,5 °C"
    print("ok")
