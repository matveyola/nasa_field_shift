"""Verified citations: resolve {{cite:<chunk_id>}} tokens against this turn's retrieved evidence
and check that each cited sentence is plausibly supported by the passage it cites.

Checked (deterministic, offline):
- the chunk exists in the index and was returned by search_knowledge in this turn;
- an optional exact quote ({{cite:id|"..."}}) is a real span of the chunk (whitespace-insensitive);
- every number the grounding policy treats as a claim, in a citing sentence, occurs in a cited
  passage; for tables, in the row of a crop named in the sentence and, when a growth stage or
  column is named, in that column;
- a citing sentence that also talks about "your field" may not carry source numbers
  (general FAO values are not measurements of this field);
- the citing sentence shares vocabulary with the passage (English, or Ukrainian via the glossary).

Not checked: whether the sentence's reasoning follows from the passage.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from contracts.models import Citation

from .knowledge.glossary import rewrite, tokens
from .knowledge.parse_pdf import _DOC_PARTS
from .knowledge.retrieve import ChunkRecord
from .knowledge.sources import PUBLICATIONS

CITE_TOKEN = re.compile(r"\{\{\s*cite\s*:\s*([^{}|\s]{1,128})\s*(?:\|\s*\"([^\"{}]{1,300})\"\s*)?\}\}")
URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?![\s]|$))*(?:[.!?]+(?=\s|$)|\n|$)")  # a '.' ends a sentence only before whitespace: '1.20' does not
FIELD_WORDS = re.compile(r"\b(?:your|this|my)\s+field\b|вашому\s+пол|вашого\s+пол|на\s+вашому|вашій\s+ділянц", re.IGNORECASE)
CITATION_LICENCE = "© FAO 1998. Non-commercial use with acknowledgement of FAO as source; no FAO endorsement implied."

COLUMN_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\binit|\bini\b|початков|сход", re.IGNORECASE), "ini"),
    (re.compile(r"\bmid|середин|цвітін", re.IGNORECASE), "mid"),
    (re.compile(r"\bend\b|\blate\b|кінц|пізн|достиган|збиранн", re.IGNORECASE), "end"),
    (re.compile(r"root\s+depth|корен", re.IGNORECASE), "root depth"),
    (re.compile(r"depletion|виснаж", re.IGNORECASE), "depletion"),
    (re.compile(r"height|висот", re.IGNORECASE), "height"),
]
GENERIC_LABEL_WORDS = {"field", "fresh", "grain", "harvested", "small", "crops", "crop", "other", "spring", "winter"}


@dataclass(frozen=True)
class CitationIssue:
    category: str
    snippet: str
    start: int
    end: int


@dataclass
class CitedSentence:
    start: int
    end: int
    text: str                    # sentence without citation tokens
    chunks: list[ChunkRecord]


def values(text: str) -> set[float]:
    out = set()
    for m in _NUM.finditer(text):
        try:
            out.add(round(float(m.group(0).replace(",", ".")), 6))
        except ValueError:
            pass
    return out


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def stems(text: str, ukrainian: bool = False) -> set[str]:
    """Five-letter stems of the English words (after the uk->en glossary); with `ukrainian`, also of the Ukrainian
    words (for Ukrainian sources, which share no English words with a question)."""
    original, expanded = rewrite(text)
    words = [w.lower() for w in original + expanded if not re.search(r"[Ѐ-ӿ]", w)]
    if ukrainian:
        words += [w for w in tokens(text) if re.search(r"[Ѐ-ӿ]", w)]
    return {w[:5] for w in words if len(w) >= 3}


def _is_uk(chunk: ChunkRecord) -> bool:
    return chunk.doc_id.startswith("naas-")


def uk_locator(section: str) -> str:
    """A Ukrainian booklet's section path for people: a booklet-level first part ("ВСТУП") is dropped when a more
    specific heading follows ("ВСТУП > Соняшник" -> "Соняшник"); chapters and crops stay."""
    head, sep, rest = section.partition(" > ")
    return rest if sep and head.startswith(_DOC_PARTS) else section


# Words that say nothing about what a passage is about (question scaffolding, the source's own name).
_GENERIC = {"fao", "fao56", "what", "does", "about", "say", "says", "tell", "how", "which", "the", "and", "for",
            "with", "this", "that", "from", "are", "can", "should", "will", "field", "farm", "crop", "crops", "guide"}


def unrelated_chunks(question: str, chunks: list[ChunkRecord]) -> list[str]:
    """Cited chunks that share no content term with the farmer's question (after the uk->en glossary).

    A weak relevance signal, not a judgement: an empty list means "no obvious mismatch". When the
    question has no usable terms (only Ukrainian words outside the glossary) nothing can be judged.
    """
    def terms(ukrainian: bool) -> set[str]:
        return {t for t in stems(question, ukrainian) if t not in {g[:5] for g in _GENERIC} and not t.isdigit()}

    out = []
    for c in chunks:
        wanted = terms(_is_uk(c))
        if wanted and not wanted & stems(c.index_text, _is_uk(c)):
            out.append(c.chunk_id)
    return out


def resolve(text: str, evidence: dict[str, ChunkRecord],
            index_has: Callable[[str], bool]) -> tuple[list[CitationIssue], list[CitedSentence]]:
    issues = [CitationIssue("unverified_url", m.group(0)[:60], m.start(), m.end()) for m in URL.finditer(text)]
    sentences: list[CitedSentence] = []
    for sentence in SENTENCE.finditer(text):
        tokens = list(CITE_TOKEN.finditer(sentence.group(0)))
        if not tokens:
            continue
        cited: list[ChunkRecord] = []
        for t in tokens:
            cid, quote = t.group(1), t.group(2)
            span = (sentence.start() + t.start(), sentence.start() + t.end())
            if cid not in evidence:
                category = "citation_not_retrieved" if index_has(cid) else "fabricated_citation"
                issues.append(CitationIssue(category, t.group(0)[:60], *span))
            elif quote and _norm_ws(quote) not in _norm_ws(evidence[cid].text):
                issues.append(CitationIssue("fabricated_quote", quote[:60], *span))
            elif evidence[cid] not in cited:
                cited.append(evidence[cid])
        if cited:
            sentences.append(CitedSentence(sentence.start(), sentence.end(),
                                           CITE_TOKEN.sub(" ", sentence.group(0)), cited))
    return issues, sentences


def _table_rows(chunk: ChunkRecord) -> list[dict[str, str]]:
    rows = []
    for line in chunk.text.split("\n")[1:]:
        if " | " not in line and not line.startswith("Crop:"):
            continue
        row = {}
        for i, part in enumerate(line.split(" | ")):
            key, sep, value = part.partition(": ")
            row[key.strip().lower() if sep else f"_label{i}"] = (value if sep else key).strip()
        rows.append(row)
    return rows


def _named_rows(chunks: list[ChunkRecord], sentence: str) -> list[dict[str, str]]:
    """Rows whose crop the sentence names. When several match (Maize, Field / Maize, Sweet), keep
    those whose qualifier words ("field", "sweet", "grain") also appear in the sentence."""
    sentence_stems = stems(sentence)
    named: list[tuple[int, dict[str, str]]] = []
    for chunk in chunks:
        if chunk.kind != "table":
            continue
        for row in _table_rows(chunk):
            label = next(iter(row.values()), "")
            words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", label)]
            core = [w for w in words if w not in GENERIC_LABEL_WORDS]
            if core and core[0][:5] in sentence_stems:
                named.append((len({w[:5] for w in words} & sentence_stems), row))
    if not named:
        return []
    best = max(score for score, _ in named)
    return [row for score, row in named if score == best]


def number_problem(sentence: str, number: str, cited: list[ChunkRecord]) -> str | None:
    """None when `number` is backed by the cited passages; otherwise the violation category."""
    value = values(number)
    if not value:
        return None
    value = next(iter(value))
    if not any(value in values(c.text) for c in cited):
        return "number_not_in_source"
    if any(c.kind != "table" and value in values(c.text) for c in cited):
        return None  # stated in prose: no row structure to check
    named = _named_rows(cited, sentence)
    if not named:
        return None
    if not any(value in values(" ".join(r.values())) for r in named):
        return "wrong_table_row"
    columns = [col for pattern, col in COLUMN_WORDS if pattern.search(sentence)]
    if columns and not any(value in values(v) for r in named for k, v in r.items() if any(c in k for c in columns)):
        return "wrong_table_column"
    return None


def sentence_problem(sentence: CitedSentence, has_claim_numbers: bool) -> str | None:
    if has_claim_numbers and FIELD_WORDS.search(sentence.text):
        return "general_source_as_field_data"
    uk = any(_is_uk(c) for c in sentence.chunks)
    claim = stems(sentence.text, uk)
    if len(claim) >= 3 and not claim & set().union(*(stems(c.index_text, uk) for c in sentence.chunks)):
        return "citation_unrelated"
    return None


def best_quote(chunk: ChunkRecord, claim: str) -> str:
    """The chunk sentence or table row that best overlaps the claim: an exact span, at most 300 chars."""
    pieces = [p.strip() for p in re.split(r"(?<=[.;])\s+|\n", chunk.text) if len(p.strip()) > 3]
    claim_stems, claim_values = stems(claim), values(claim)
    best = max(pieces, key=lambda p: (len(values(p) & claim_values), len(stems(p) & claim_stems), -abs(len(p) - 160)),
               default=chunk.text)
    if len(best) <= 300:
        return best
    cut = best.rfind(" ", 0, 300)
    return best[:cut if cut > 0 else 300].rstrip()


def to_citation(n: int, chunk: ChunkRecord, quote: str) -> Citation:
    publication = PUBLICATIONS[chunk.publication_id]
    # Ukrainian booklets: the whole path ("ОЗИМЕ ТРИТИКАЛЕ > Строки та норми сівби"), so the crop is always named
    locator = {"chapter": chunk.chapter or None,
               "section": (uk_locator(chunk.section) if _is_uk(chunk) and chunk.section else chunk.heading)[:300]}
    if chunk.table:
        locator["table"] = chunk.table
    if chunk.anchor and chunk.anchor.startswith("page="):
        locator["page"] = int(chunk.anchor.removeprefix("page="))
    return Citation.model_validate_json(json.dumps({
        "id": str(n), "chunk_id": chunk.chunk_id, "doc_id": chunk.doc_id,
        "title": f"{publication.title} ({publication.series})"[:300], "publisher": publication.publisher,
        "year": publication.year, "locator": locator, "url": chunk.url, "quote": quote,
        "language": publication.language, "licence": publication.citation_licence or CITATION_LICENCE,
    }, ensure_ascii=False))
