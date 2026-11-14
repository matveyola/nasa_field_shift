"""Parse a text PDF (Ukrainian recommendation booklets) into the same blocks as the HTML parser.

Each page's text becomes paragraphs tagged with their page (block.anchor = "page=N"), so every chunk stays on one
page and its citation can name the page and link to it (#page=N). Short stand-alone lines become headings.

Known limit: pypdf drops superscripts, so "2–3°" may read "2-30"; that is why citations always name the page and
the farmer can check the original. Documents whose text falls apart into single characters are not added at all
(see sources.py).
"""

import io
import re

from pypdf import PdfReader

from .parse import Block, ParseError

_BULLETS = str.maketrans({"": "•", "": "•", "": "•", " ": " "})
_PAGE_NUMBER = re.compile(r"^\s*\d{1,3}\s*$")
_ENDS_SENTENCE = re.compile(r"[.:;!?»)]\s*$")
_STARTS_NEW = re.compile(r"^\s*(?:[А-ЯІЇЄҐA-Z0-9«(]|[–—•-]\s)")


# Parts of the whole booklet, not of the chapter before them ("УЗАГАЛЬНЕННЯ ТА КЛЮЧОВІ ВИСНОВКИ" after chapter 7).
_DOC_PARTS = ("ВСТУП", "ЗМІСТ", "УЗАГАЛЬНЕННЯ", "ВИСНОВКИ", "ДОДАТ", "ПЕРЕДМОВА", "СПИСОК")


def _caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


def _heading(line: str) -> bool:
    if _caps(line) and len(line) <= 90 and "…" not in line and "..." not in line:
        return True  # chapter titles: capitals, maybe numbered ("3. ТЕХНОЛОГІЧНІ ОСОБЛИВОСТІ ... ОЗИМОГО"); not contents lines
    words = line.split()
    return (1 <= len(words) <= 6 and len(line) <= 60 and line[:1].isupper() and not re.search(r"\d", line)
            and not line.endswith((",", ";", "-", "–")) and not re.search(r"\b[А-ЯІЇЄҐ]\.[А-ЯІЇЄҐ]\.", line))


def _paragraphs(lines: list[str]) -> list[tuple[str, str]]:
    """(kind, text) per paragraph: lines are joined back; a line ending a sentence followed by one that starts a new
    one (capital letter, dash or bullet) ends the paragraph. A hyphen at a line end is kept and joined (compounds
    such as "дисково-чизельний" stay right; split syllables keep a visible hyphen)."""
    out: list[tuple[str, str]] = []
    current = ""
    for i, raw in enumerate(lines):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        numbered = _caps(line) and re.match(r"^\d+\.\s", line) is not None  # "4. ОСНОВНІ АСПЕКТИ ...": always a new chapter
        if _heading(line) and (numbered or not current or _ENDS_SENTENCE.search(current) or prev_blank):
            if current:
                out.append(("paragraph", current))
            # A label ending in ":" or "." ("Строки та норми сівби:") is a subsection of the heading before it (usually
            # the crop, "ОЗИМЕ ТРИТИКАЛЕ"), so the crop stays in the chunk's section path and in its citation.
            # A numbered capitalised title ("3. ... ОЗИМОГО ЯЧМЕНЮ") is a chapter (level 2); other capitals a part of it
            # (a crop "ОЗИМЕ ТРИТИКАЛЕ" or a zone "ЛІСОСТЕП", 3); other short titles a section (4); a label ending in ":"
            # or "." its subsection (5). So the crop stays in every chunk's section path.
            kind = ("chapter" if _caps(line) and (re.match(r"^\d+\.", line) or line.startswith(_DOC_PARTS))
                    else "heading" if _caps(line)
                    else "subheading" if line.endswith((":", ".")) else "section")
            if kind == "heading" and not current and out and out[-1][0] in ("chapter", "heading") and _caps(line):
                out[-1] = (out[-1][0], f"{out[-1][1]} {line}")  # one capitalised heading printed on two lines
            else:
                out.append((kind, line.rstrip(".:")))
            current = ""
            continue
        if current and _ENDS_SENTENCE.search(current) and _STARTS_NEW.match(line):
            out.append(("paragraph", current))
            current = line
        elif current.endswith(("-", "–")):
            current += line
        else:
            current = f"{current} {line}" if current else line
    if current:
        out.append(("paragraph", current))
    return out


def parse_pdf(raw: bytes) -> list[Block]:
    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as e:  # malformed or encrypted
        raise ParseError(f"unreadable PDF: {type(e).__name__}") from e
    blocks: list[Block] = []
    for n, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").translate(_BULLETS)
        if len(re.findall(r"[а-яіїєґ]", text.lower())) < 80:
            continue  # cover, picture or a page whose fonts do not extract: nothing to quote
        lines = [ln for ln in text.splitlines() if not _PAGE_NUMBER.match(ln)]
        for kind, content in _paragraphs(lines):
            if kind in ("chapter", "heading", "section", "subheading"):
                blocks.append(Block(kind="heading", text=content, anchor=f"page={n}",
                                    level={"chapter": 2, "heading": 3, "section": 4, "subheading": 5}[kind]))
            else:
                blocks.append(Block(kind="paragraph", text=content, anchor=f"page={n}"))
    if not blocks:
        raise ParseError("no readable text in the PDF")
    return blocks
