"""Deterministic query rewriting for the English lexical index (no LLM).

The corpus is English; BM25 cannot match a Ukrainian query. These stem -> English term
expansions cover general FAO-56 / agronomy vocabulary and the crops of FAO-56 Tables 11-12, 17,
22-24. The list was written before the retrieval benchmark and is not tuned to its questions.
The dense model receives the original query; only the lexical leg sees the rewrite.
"""

import re

# Ukrainian stem (lowercase prefix) -> English search terms.
UK_STEMS: dict[str, str] = {
    # crops
    "кукурудз": "maize corn", "пшениц": "wheat", "ячмен": "barley", "ячмін": "barley", "соняшник": "sunflower",
    "соя": "soybeans", "сої": "soybeans", "сою": "soybeans", "горох": "peas", "ріпак": "rapeseed canola",
    "картопл": "potato", "буряк": "beets sugar", "томат": "tomato", "помідор": "tomato", "капуст": "cabbage",
    "морк": "carrots", "цибул": "onions", "огір": "cucumber", "квасол": "beans", "люцерн": "alfalfa",
    "сорго": "sorghum", "овес": "oats", "вівс": "oats", "жит": "rye", "рис": "rice", "бавовн": "cotton",
    "виноград": "grapes", "ябл": "apples", "трав": "grass", "газон": "turf grass", "льон": "flax", "нут": "chickpea",
    "сочевиц": "lentil", "кавун": "watermelon", "дин": "melon", "перець": "peppers", "перц": "peppers",
    "часник": "garlic", "салат": "lettuce", "шпинат": "spinach", "оливк": "olives", "цитрус": "citrus",
    "пасовищ": "pasture grazing", "зерн": "grain", "солодк": "sweet", "сад": "orchard fruit trees", "озим": "winter", "яр": "spring",
    # crop water use and coefficients
    "евапотранспірац": "evapotranspiration", "випаровуван": "evaporation", "транспірац": "transpiration",
    "водоспожив": "crop water requirements evapotranspiration ETc", "потреб": "requirements",
    "коефіцієнт": "coefficient Kc", "базов": "basal Kcb", "еталон": "reference ETo", "референ": "reference ETo",
    # growth stages
    "фаз": "stage", "стаді": "stage", "період": "period stage", "початков": "initial", "розвит": "development",
    "середин": "mid-season", "цвітін": "flowering mid-season", "кінц": "end late", "пізн": "late",
    "достиган": "maturity late season", "збиранн": "harvest", "тривал": "length duration", "сходи": "emergence",
    "посів": "planting sowing", "сів": "planting sowing",
    # soil water
    "корен": "root", "глибин": "depth", "зон": "zone", "доступн": "available", "волог": "water moisture",
    "ґрунт": "soil", "грунт": "soil", "виснаж": "depletion", "дефіцит": "depletion deficit", "запас": "available water",
    "польов": "field capacity", "в'янен": "wilting point", "вʼянен": "wilting point", "текстур": "texture",
    "піщан": "sand", "глин": "clay", "суглин": "loam",
    # stress, climate, management
    "водн": "water", "стрес": "stress Ks", "посух": "water stress drought", "засолен": "salinity", "солон": "salinity saline",
    "полив": "irrigation", "зрошен": "irrigation", "опад": "rainfall precipitation", "дощ": "rainfall rain",
    "вітр": "wind", "вітер": "wind", "вологіст": "humidity moisture", "клімат": "climate", "посушлив": "arid",
    "висот": "height", "врожа": "yield", "урожа": "yield", "зниж": "reduction", "рівнян": "equation",
    "формул": "equation", "табли": "table", "температур": "temperature", "мульч": "mulch", "покрив": "cover",
    "щільн": "density", "добрив": "fertility", "випаровуванн": "evaporation", "поверхн": "surface",
}

# Abbreviation spellings people use for the FAO symbols.
ABBREVIATIONS = {"et0": "ETo", "ето": "ETo", "ет0": "ETo", "етс": "ETc", "кс": "Kc", "kc0": "Kc"}

STOPWORDS = {
    # English
    "the", "a", "an", "of", "for", "to", "in", "on", "at", "is", "are", "was", "be", "how", "what", "which", "when",
    "does", "do", "and", "or", "by", "with", "from", "as", "it", "its", "this", "that", "under", "during", "about",
    "can", "should", "my", "i", "me", "there", "their", "into", "per", "vs", "between", "than",
    # Ukrainian
    "як", "що", "який", "яка", "яке", "які", "яких", "для", "під", "час", "на", "у", "в", "і", "й", "та", "з", "із",
    "до", "по", "при", "це", "чи", "є", "мій", "моє", "моя", "мені", "скільки", "коли", "чому", "від", "про", "або",
    "між", "ніж", "так", "не", "ж", "же", "би", "б", "буде", "бути", "змінюється", "залежить",
}

# Search terms only (not citation checks): every passage is FAO-56, so the corpus name carries no information,
# and "56" matched unrelated numbers (equation values) and outranked the relevant passage.
CORPUS_NAME = {"fao", "фао", "56", "fao56"}

_TOKEN = re.compile(r"[\w'ʼ]+", re.UNICODE)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1 or t in ("p",)]


# Question words that say nothing about the subject (Ukrainian sources only; FAO terms go through UK_STEMS).
_UK_FILLER = {"таке", "такий", "така", "які", "який", "яка", "яке", "коли", "чому", "скільки", "можна", "треба",
              "потрібно", "мені", "моєму", "моє", "мого", "поле", "полі", "поля", "цьому", "цього", "зараз", "вже", "розкажи",
              "покажи", "порадь", "підкажи", "кажуть", "рекомендації", "рекомендують"}


# Crop stems of UK_STEMS long enough to search as prefixes without hitting unrelated words.
_UK_CROPS = {"кукурудз", "пшениц", "ячмен", "ячмін", "соняшник", "горох", "ріпак", "картопл", "буряк", "люцерн",
             "сорго", "сочевиц", "квасол"}


def uk_prefixes(query: str) -> list[str]:
    """Ukrainian search terms for the Ukrainian sources, as FTS prefixes ending in "*" or exact words.

    A word the FAO glossary knows (a science term such as "водний стрес", or a crop such as "сою") is searched
    exactly: its stem would also hit loose words ("стре*" -> "стресового"). Any other word is cut to a stem
    ("соняшнику" -> "соняшни*", "сіяти" -> "сіят*") so its inflected forms match. ponytail: a cut, not a
    morphological analyser; add one if recall on Ukrainian sources is not enough."""
    out = []
    for token in tokens(query):
        if not _CYRILLIC.search(token) or token in CORPUS_NAME or token in _UK_FILLER:
            continue
        stem = next((s for s in UK_STEMS if token.startswith(s)), None)
        if stem in _UK_CROPS:
            out.append(stem + "*")  # crops: every form ("пшениц*" for пшениця / пшениці / пшеницю)
        else:
            out.append(token if stem or len(token) <= 4 else token[: max(4, len(token) - 2)] + "*")
    return list(dict.fromkeys(out))


def rewrite(query: str) -> tuple[list[str], list[str]]:
    """Return (original search terms, English expansions) for the lexical index."""
    original, expanded = [], []
    for token in tokens(query):
        if token in ABBREVIATIONS:
            expanded.append(ABBREVIATIONS[token])
            continue
        if _CYRILLIC.search(token):
            hits = [terms for stem, terms in UK_STEMS.items() if token.startswith(stem)]
            for terms in hits:
                expanded.extend(terms.split())
            if not hits:
                original.append(token)  # keep: may still match a transliterated name
        else:
            original.append(token)
    return original, list(dict.fromkeys(expanded))
