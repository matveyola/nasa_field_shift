"""Farm-budget arithmetic on the farmer's own figures (Task 05A, 2026-11-14).

The language model never calculates. It names an operation and quotes the farmer's words for each operand; this
module finds that quote in the farmer's CURRENT message, reads the one number and its unit from the farmer's own
text, and does exact decimal arithmetic. History, the field's data and the model's own numbers are never operands.

Three operations, nothing else: a total divided by an area (per hectare), a per-hectare amount times an area, and one
amount minus another in the same unit. Only UAH and USD, only hectares, no conversions. Anything ambiguous (a number
format that reads two ways, a missing or unknown unit, two numbers in one quote, a cut-off number, "thousand" outside
the quote) is refused with a reason the model can pass on, never guessed.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

Operation = Literal["per_hectare", "total_for_area", "difference"]
OperandUnit = Literal["UAH", "USD", "UAH/ha", "USD/ha", "ha"]
CURRENCIES = ("UAH", "USD")

MAX_MONEY = Decimal("100000000000")   # 100 bn: far above any farm budget; larger is a typo or a different question
MAX_AREA_HA = Decimal("1000000")      # 1 m ha: larger than the biggest farm groups
CENT = Decimal("0.01")


class BudgetRefused(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class Operand:
    value: Decimal
    unit: OperandUnit
    quote: str          # the farmer's words, as found in their message (whitespace normalised)


@dataclass(frozen=True)
class Result:
    value: Decimal
    unit: OperandUnit
    expression: str     # "300000 UAH / 40 ha = 7500.00 UAH/ha"


# ------------------------------------------------------------------ reading the farmer's text

_SPACES = re.compile(r"[\s    ]+")
# A run of digits with separators; a separator or space belongs to the number only when a digit follows ("300 000",
# "40,5"), so the full stop or comma that ends a sentence is not part of it.
_RUN = re.compile(r"\d(?:\d|[.,](?=\d)|[ ](?=\d))*")
_WORDS = r"тис\.?|тисяч\w*|млн\.?|мільйон\w*|мільярд\w*|млрд\.?|thousand|million|billion|mln|bn"
_SCALE = re.compile(rf"\s*({_WORDS}|k)(?![^\W\d_])", re.IGNORECASE)          # right after the number: "300 тис.", "300k"
_SCALE_WORD = re.compile(rf"(?<![^\W\d_])(?:{_WORDS})(?![^\W\d_])", re.IGNORECASE)  # anywhere else in the quote
_SCALES = {"тис": 3, "тисяч": 3, "thousand": 3, "k": 3, "млн": 6, "мільйон": 6, "million": 6, "mln": 6,
           "млрд": 9, "мільярд": 9, "billion": 9, "bn": 9}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

_UAH = re.compile(r"грн|гривн\w*|гривен\w*|₴|\buah\b|hryvn\w*", re.IGNORECASE)
_USD = re.compile(r"\$|\busd\b|долар\w*|\bдол\b\.?|\bdollars?\b", re.IGNORECASE)
_OTHER_CURRENCY = re.compile(r"€|\beur\b|євро|\beuros?\b|£|\bgbp\b|злот\w*|\bpln\b|руб\w*|\brub\b", re.IGNORECASE)
_HECTARE = re.compile(r"\bга\b|гектар\w*|\bha\b|hectares?", re.IGNORECASE)
_PER_HECTARE = re.compile(r"(?:/|\bза\s+|\bна\s+|\bper\s+|\ba\s+|\ban\s+)(?:1\s+|один\s+|одного\s+|one\s+)?"
                          r"(?:га\b|гектар\w*|ha\b|hectare)", re.IGNORECASE)
_OTHER_AREA = re.compile(r"сот(?:ок|ка|ки)|\bакр\w*|\bacres?\b|\bm2\b|м²|м2|км²", re.IGNORECASE)
# Right after a quoted total: "5 500 грн | на гектар" means the farmer gave a per-hectare amount.
_PER_HECTARE_AFTER = re.compile(r"\s*(?:/|(?:за|на|per|a|an)\s+)(?:1\s+|один\s+|одного\s+|one\s+)?"
                                r"(?:га|гектар\w*|ha|hectares?)(?![^\W\d_])", re.IGNORECASE)
# Letters glued to a number ("40га") must be a unit; "2m", "5кк" or "$2 M" mean millions in a way this tool won't guess.
_UNIT_WORD = re.compile(r"га|ha|грн|uah|usd|гектар\w*|hectares?|гривн\w*|гривен\w*|долар\w*|dollars?", re.IGNORECASE)
_MILLION_ABBR = {"m", "mn", "mio", "mil", "mm", "к", "кк", "лям", "ляма", "лямів"}


def normalize(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def parse_number(run: str, style: Literal["uk", "en"]) -> Decimal:
    """One written number, or BudgetRefused when it can be read two ways.

    Unambiguous: plain digits; space-grouped thousands ("1 250 000"); a decimal part of one or two digits after "," or
    "." ("40,5", "7500.50"); repeated separators with three-digit groups ("1,000,000"). A single "," or "." followed by
    exactly three digits reads both ways ("300,000" is 300 thousand in English, 300.000 in Ukrainian): accepted only
    as an English thousands comma, otherwise refused.
    """
    s = run.strip()
    decimals = ""
    m = re.fullmatch(r"(.+?)[.,](\d{1,2})", s)
    if m and not re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        s, decimals = m.group(1), m.group(2)
    if re.fullmatch(r"\d+", s):
        integer = s
    elif re.fullmatch(r"\d{1,3}(?: \d{3})+", s):
        integer = s.replace(" ", "")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}", s) or re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", s):
        integer = s.replace(",", "").replace(".", "")
    elif re.fullmatch(r"\d{1,3},\d{3}", s) and style == "en" and not decimals:
        integer = s.replace(",", "")
    elif style == "en" and decimals and re.fullmatch(r"\d{1,3}(?:,\d{3})+", s) and "." in run[-3:]:
        integer = s.replace(",", "")  # "1,250.50"
    else:
        raise BudgetRefused("ambiguous_number", f"\"{run}\" can be read in more than one way. Ask the farmer to write "
                            "the amount in full, for example 300000 or 300 000.")
    if len(integer) > 1 and integer.startswith("0"):
        raise BudgetRefused("ambiguous_number", f"\"{run}\" has a leading zero. Ask the farmer for the exact amount.")
    if len(integer) > 13:
        raise BudgetRefused("too_large", "That number is too large to be a farm amount. Ask the farmer to check it.")
    return Decimal(f"{integer}.{decimals or '0'}")


def _unit(words: str) -> OperandUnit:
    if _OTHER_CURRENCY.search(words):
        raise BudgetRefused("unsupported_currency", "Only hryvnias (UAH) and US dollars (USD) are supported, and they are "
                            "never converted. Say this amount cannot be calculated here.")
    if _OTHER_AREA.search(words):
        raise BudgetRefused("unsupported_unit", "Only hectares are supported. Ask the farmer for the area in hectares.")
    uah, usd = bool(_UAH.search(words)), bool(_USD.search(words))
    if uah and usd:
        raise BudgetRefused("currency_mismatch", "This quote names two currencies. Quote one amount at a time.")
    currency = "UAH" if uah else "USD" if usd else None
    if currency and _PER_HECTARE.search(words):
        return f"{currency}/ha"  # type: ignore[return-value]
    if currency:
        return currency  # type: ignore[return-value]
    if _HECTARE.search(words):
        return "ha"
    raise BudgetRefused("missing_unit", "The quote must include the unit the farmer wrote (грн, UAH, $, USD, га, ha). "
                        "If the farmer gave no unit, ask instead of assuming one.")


def read_operand(quote: str, declared: OperandUnit, farmer_message: str) -> Operand:
    """The one number and unit the farmer wrote in `quote`, found in their current message."""
    message, q = normalize(farmer_message), normalize(quote)
    if not q or q not in message:
        raise BudgetRefused("evidence_not_found", "Quote the farmer's exact words from their current message (number "
                            "and unit). Figures from earlier messages, the app or your own estimates are not inputs: "
                            "ask the farmer to give them in this message.")
    style: Literal["uk", "en"] = "uk" if _CYRILLIC.search(message) else "en"
    runs = [(m.start(), m.end()) for m in _RUN.finditer(message)]
    readings = set()
    for start in (i for i in range(len(message)) if message.startswith(q, i)):
        end = start + len(q)
        inside = [(a, b) for a, b in runs if a >= start and b <= end]
        if any(a < end and b > start and (a, b) not in inside for a, b in runs):
            raise BudgetRefused("partial_number", "The quote cuts a number in two. Quote the whole amount.")
        if len(inside) != 1:
            raise BudgetRefused("one_number_per_quote", "Each quote must contain exactly one number with its unit, for "
                                "example \"300 000 грн\" and \"40 га\" as two separate quotes.")
        a, b = inside[0]
        if a > 0 and message[a - 1] in "-−":
            raise BudgetRefused("invalid_value", "Negative amounts are not supported. Ask the farmer what they mean.")
        scale = _SCALE.match(message, b)
        if scale and scale.end() > end:
            raise BudgetRefused("partial_number", f"The farmer wrote \"{scale.group(1)}\" after this number: include it "
                                "in the quote.")
        tail = scale.end() if scale else b
        if _SCALE_WORD.search(message[tail:end]) or _SCALE_WORD.search(message[start:a]):
            raise BudgetRefused("ambiguous_number", "The quote has a scale word that is not right after the number.")
        glued = None if scale else re.match(r"[^\W\d_]+", message[tail:])
        stray = re.match(r"\s+([^\W\d_]{1,6})\.?(?![^\W\d_])", message[tail:])
        if (glued and not _UNIT_WORD.fullmatch(glued.group(0))) or (stray and stray.group(1).lower() in _MILLION_ABBR):
            word = glued.group(0) if glued else stray.group(1)
            raise BudgetRefused("ambiguous_number", f"\"{word}\" after the number is not a unit this tool reads and may "
                                "mean thousands or millions. Ask the farmer to write the amount in full.")
        value = parse_number(message[a:b], style)
        if scale:
            word = scale.group(1).lower().rstrip(".")
            power = next(p for key, p in _SCALES.items() if word.startswith(key))
            value = value.scaleb(power)
        unit = _unit(message[start:a] + " " + message[b:end])
        if unit in CURRENCIES and _PER_HECTARE_AFTER.match(message, end):
            raise BudgetRefused("partial_number", "The farmer wrote \"per hectare\" right after this amount: include it in "
                                "the quote and use the per-hectare unit.")
        readings.add((value, unit))
    if len(readings) != 1:
        raise BudgetRefused("ambiguous_number", "These words appear more than once with different meanings. Quote more "
                            "of the sentence.")
    value, unit = readings.pop()
    if unit != declared:
        raise BudgetRefused("unit_mismatch", f"The farmer's words give the unit {unit}, not {declared}. Use the unit the "
                            "farmer wrote; never assume another one.")
    return Operand(value=value, unit=unit, quote=q)


# ------------------------------------------------------------------ arithmetic


def _check_size(o: Operand) -> None:
    if o.unit == "ha":
        if o.value <= 0:
            raise BudgetRefused("zero_area", "The area is zero. Ask the farmer for the area in hectares.")
        if o.value > MAX_AREA_HA:
            raise BudgetRefused("too_large", "That area is larger than any farm. Ask the farmer to check it.")
    elif o.value > MAX_MONEY:
        raise BudgetRefused("too_large", "That amount is too large to be a farm amount. Ask the farmer to check it.")


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f") if d == d.to_integral() else format(d, "f")


def calculate(operation: Operation, amount: Operand, other: Operand) -> Result:
    for o in (amount, other):
        _check_size(o)
    if operation in ("per_hectare", "total_for_area"):
        wanted = CURRENCIES if operation == "per_hectare" else tuple(f"{c}/ha" for c in CURRENCIES)
        if amount.unit not in wanted:
            raise BudgetRefused("unit_mismatch", f"For {operation}, the amount must be in {' or '.join(wanted)}; the "
                                f"farmer's words give {amount.unit}.")
        if other.unit != "ha":
            raise BudgetRefused("unit_mismatch", f"For {operation}, the second quote must be an area in hectares; the "
                                f"farmer's words give {other.unit}.")
        currency = amount.unit.split("/")[0]
        if operation == "per_hectare":
            value, unit, sign = amount.value / other.value, f"{currency}/ha", "/"
        else:
            value, unit, sign = amount.value * other.value, currency, "×"
    else:
        if "ha" in (amount.unit, other.unit):
            raise BudgetRefused("unit_mismatch", "A difference is only between two money amounts in the same unit.")
        if amount.unit != other.unit:
            raise BudgetRefused("currency_mismatch", f"{amount.unit} and {other.unit} cannot be combined: currencies are "
                                "never converted and totals are not mixed with per-hectare amounts. Ask the farmer.")
        value, unit, sign = amount.value - other.value, amount.unit, "−"
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if abs(value) > MAX_MONEY:
        raise BudgetRefused("too_large", "The result is too large to be a farm amount. Ask the farmer to check the inputs.")
    expression = f"{_plain(amount.value)} {amount.unit} {sign} {_plain(other.value)} {other.unit} = {value} {unit}"
    return Result(value=value, unit=unit, expression=expression)  # type: ignore[arg-type]
