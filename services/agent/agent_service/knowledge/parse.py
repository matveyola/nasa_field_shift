"""Parse one FAO-56 HTML page into ordered, structured blocks.

Keeps: heading hierarchy and anchors, paragraphs, numbered equations, data tables as grids
(row/col spans resolved, group rows carried down, header labels per column, footnotes attached),
example boxes, figure captions. Drops: navigation, section link lists, decorative images.
Images with text are replaced by verified transcriptions or by an explicit placeholder.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, NavigableString, Tag

from .sources import FIGURE_IMAGES, VERIFIED_IMAGE_TEXT

UNREADABLE_IMAGE = "[image not transcribed]"
INLINE_TAGS = {"a", "b", "i", "u", "em", "strong", "sub", "sup", "font", "span", "img", "br", "small", "big", "code", "tt"}
NAV_IMAGES = {"back.gif", "toc.gif", "next.gif"}

# Adobe Symbol font code points used for Greek letters and operators in the FAO HTML.
SYMBOL_FONT = {
    "a": "α", "b": "β", "c": "χ", "d": "δ", "D": "Δ", "e": "ε", "f": "φ", "g": "γ", "G": "Γ", "h": "η", "l": "λ",
    "m": "μ", "p": "π", "q": "θ", "Q": "Θ", "r": "ρ", "s": "σ", "S": "Σ", "t": "τ", "w": "ω", "W": "Ω", "y": "ψ",
    "»": "≈", "³": "≥", "£": "≤", "´": "×", "®": "→", "¾": "—", "¹": "≠", "º": "≡", "Ö": "√", "×": "·", "-": "−",
    "±": "±", "°": "°", "<": "<", ">": ">", "=": "=", "+": "+", "(": "(", ")": ")", " ": " ",
}
EQUATION_NUMBER = re.compile(r"\((\d{1,3}[a-z]?)\)\s*$")
TABLE_CAPTION = re.compile(r"^TABLE\s+(\d+)\.\s*(.*)$", re.DOTALL)
VALUE = re.compile(r"^[\s~≈<>≤≥±–\-+.…]*\d")          # cell starts with a number: a data value
GROUP_LABEL = re.compile(r"^[a-z]\.\s+\S")            # lettered crop groups: "a. Small Vegetables"


@dataclass
class Row:
    cells: list[str | None]          # one entry per column; None = continuation of a colspan cell
    group: str | None = None         # group label in force for this row (e.g. "a. Small Vegetables")
    is_group: bool = False


@dataclass
class Table:
    number: str | None
    caption: str
    columns: list[str]
    rows: list[Row]
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems


@dataclass
class Block:
    kind: Literal["heading", "paragraph", "equation", "table", "example", "figure"]
    text: str
    level: int | None = None
    anchor: str | None = None
    table: Table | None = None
    images: list[str] = field(default_factory=list)       # image files referenced
    transcribed: list[str] = field(default_factory=list)  # images replaced by verified transcriptions


class ParseError(Exception):
    pass


def decode(raw: bytes) -> str:
    """The pages declare iso-8859-1 but use Windows-1252 punctuation; cp1252 is the safe superset."""
    return raw.decode("cp1252", errors="replace")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _Text:
    """Inline rendering: subscripts joined (K + c ini -> 'Kc ini'), superscripts as '^n'."""

    def __init__(self):
        self.images: list[str] = []
        self.transcribed: list[str] = []

    def render(self, node) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        name = node.name.lower()
        if name in ("script", "style"):
            return ""
        if name == "br":
            return " "
        if name == "img":
            src = (node.get("src") or "").strip().lower()
            if src in NAV_IMAGES:
                return ""
            self.images.append(src)
            if src in VERIFIED_IMAGE_TEXT:
                self.transcribed.append(src)
                return f" {VERIFIED_IMAGE_TEXT[src]} "
            return " [figure] " if src in FIGURE_IMAGES else f" {UNREADABLE_IMAGE} "
        inner = "".join(self.render(c) for c in node.children)
        if name == "font" and (node.get("face") or "").lower() == "symbol":
            return "".join(SYMBOL_FONT.get(ch, ch) for ch in inner)
        if name == "sup":
            stripped = inner.strip()
            return f"^{stripped}" if stripped else ""
        return inner


def _text(node, sink: _Text) -> str:
    return _norm(sink.render(node))


def _is_nav(tag: Tag) -> bool:
    """Navigation bars and in-page link lists (a blockquote holding only links)."""
    imgs = [(i.get("src") or "").lower() for i in tag.find_all("img")]
    if imgs and all(i in NAV_IMAGES for i in imgs):
        return True
    if tag.name in ("blockquote", "p"):
        links = tag.find_all("a", href=True)
        if links:
            linked = _norm("".join(a.get_text() for a in links))
            return linked and len(linked) >= 0.9 * len(_norm(tag.get_text()))
    return False


# ------------------------------------------------------------------ tables


def _own_rows(table: Tag) -> list[Tag]:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _grid(table: Tag, sink: _Text) -> tuple[list[list[tuple[int, str]]], list[list[bool]]]:
    """Resolve row/col spans. Each slot holds (origin cell id, text); bold flags per origin cell."""
    grid: list[list[tuple[int, str] | None]] = []
    bold: list[list[bool]] = []
    origin = 0
    for r, tr in enumerate(_own_rows(table)):
        while len(grid) <= r:
            grid.append([])
            bold.append([])
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while c < len(grid[r]) and grid[r][c] is not None:
                c += 1
            text = _text(cell, sink)
            is_bold = bool(cell.find(["b", "strong"])) and _norm(" ".join(
                b.get_text() for b in cell.find_all(["b", "strong"]))) != "" and cell.name != "th"
            rows_span = max(1, int(cell.get("rowspan", 1) or 1))
            cols_span = max(1, int(cell.get("colspan", 1) or 1))
            origin += 1
            for dr in range(rows_span):
                while len(grid) <= r + dr:
                    grid.append([])
                    bold.append([])
                row = grid[r + dr]
                while len(row) < c + cols_span:
                    row.append(None)
                    bold[r + dr].append(False)
                for dc in range(cols_span):
                    row[c + dc] = (origin, text)
                    bold[r + dr][c + dc] = is_bold
            c += cols_span
    width = max((len(r) for r in grid), default=0)
    filled = [[s if s is not None else (0, "") for s in row] + [(0, "")] * (width - len(row)) for row in grid]
    flags = [b + [False] * (width - len(b)) for b in bold]
    return filled, flags


def _data_table(table: Tag, caption: str, number: str, sink: _Text) -> Table:
    grid, bold = _grid(table, sink)
    problems: list[str] = []
    if not grid:
        raise ParseError(f"table {number} has no rows")
    width = len(grid[0])

    def has_value(row) -> bool:  # a data value outside the first (label) column
        return any(VALUE.match(text) for _, text in row[1:] if text)

    def group_like(row) -> bool:
        texts = [t for _, t in row]
        return bool(texts[0]) and all(not t or t == texts[0] for t in texts[1:])

    header_count = None
    for r, row in enumerate(grid):
        if has_value(row) or (r and group_like(row)):
            header_count = r
            break
    # No value row at all (row-header tables such as Table 10): only the first row is a header.
    header_count = max(1, min(header_count if header_count is not None else 1, 4))
    columns = []
    for c in range(width):
        parts = []
        for r in range(header_count):
            text = grid[r][c][1]
            if text and text not in parts:
                parts.append(text)
        columns.append(" / ".join(parts) or f"column {c + 1}")
    if any(UNREADABLE_IMAGE in col for col in columns):
        problems.append("a column header is an image that has not been transcribed")

    rows: list[Row] = []
    group = None
    for r in range(header_count, len(grid)):
        slots = grid[r]
        # Collapse colspan continuations: one value per origin cell (None marks a continuation).
        cells = [None if c and oid and slots[c - 1][0] == oid else text for c, (oid, text) in enumerate(slots)]
        if not any(v for v in cells if v is not None):
            continue
        others = [v for v in cells[1:] if v is not None and v != cells[0]]
        # A group heading: bold first cell that is lettered ("a. Small Vegetables") or alone in its row.
        is_group = bool(cells[0]) and bold[r][0] and (bool(GROUP_LABEL.match(cells[0])) or not any(others))
        if is_group:
            group = cells[0]
        rows.append(Row(cells=cells, group=group, is_group=is_group))
    if not rows:
        problems.append("no data rows extracted")
    return Table(number=number, caption=caption, columns=columns, rows=rows, problems=problems)


def render_row(table: Table, row: Row) -> str:
    """'Crop: Broccoli | Kc ini^1: — | Kc mid: 1.05 | …' with column names repeated on every row."""
    pairs = []
    for col, value in zip(table.columns, row.cells):
        if value is None or (value == "" and col.startswith("column ")):
            continue  # colspan continuation, or an unlabeled empty cell
        pairs.append(f"{col}: {value if value else '—'}" if not col.startswith("column ") else value)
    return " | ".join(pairs)


# ------------------------------------------------------------------ page


def parse_page(raw: bytes) -> tuple[str, list[Block]]:
    soup = BeautifulSoup(decode(raw), "lxml")
    body = soup.body
    if body is None:
        raise ParseError("no <body>")
    sink = _Text()
    blocks: list[Block] = []
    title = _norm(soup.title.get_text()) if soup.title else ""

    def emit(kind, text, **kw):
        if text:
            blocks.append(Block(kind=kind, text=text, images=list(sink.images), transcribed=list(sink.transcribed), **kw))
        sink.images.clear()
        sink.transcribed.clear()

    def previous_caption() -> tuple[str, str] | None:
        for b in reversed(blocks[-3:]):
            m = TABLE_CAPTION.match(b.text) if b.kind == "paragraph" else None
            if m:
                blocks.remove(b)
                return m.group(1), b.text
        return None

    def handle_table(tag: Tag):
        imgs = {(i.get("src") or "").lower() for i in tag.find_all("img")}
        if imgs and imgs <= NAV_IMAGES:
            return
        text = _text(tag, _Text())
        if imgs & FIGURE_IMAGES and len(text.replace("[figure]", "").strip()) < 100:
            emit("figure", "[figure]")  # a chart with no real text
            return
        caption = previous_caption()
        if caption:
            number, caption_text = caption
            table = _data_table(tag, caption_text, number, sink)
            emit("table", caption_text, table=table)
            return
        if re.match(r"^\s*(EXAMPLE|BOX)\s+\d+", text):
            parts = [_text(c, sink) for c in tag.find_all(["td", "th"]) if c.find_parent("table") is tag]
            emit("example", " ".join(p for p in parts if p))
            return
        # Layout table (equations laid out in cells, side-by-side text): keep its text in order.
        rows = [" ".join(t for _, t in _dedupe(r)) for r in _grid(tag, sink)[0]]
        emit("paragraph", _norm(" ".join(rows)))

    def emit_text(text: str):
        kind = "equation" if EQUATION_NUMBER.search(text) and len(text) < 300 else "paragraph"
        emit(kind, text)

    def handle(node):
        # Loose text and inline tags in a mixed container (e.g. an unclosed <blockquote> holding a
        # footnote followed by further <p>s) are collected into one paragraph, never dropped.
        inline: list = []

        def flush_inline():
            if inline:
                text = _norm("".join(sink.render(n) for n in inline))
                inline.clear()
                if text:
                    emit_text(text)

        for child in list(node.children):
            if isinstance(child, NavigableString):
                inline.append(child)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.lower()
            if name in INLINE_TAGS:
                inline.append(child)
                continue
            flush_inline()
            if name in ("script", "style", "hr"):
                continue
            if _is_nav(child):
                continue
            if name in ("h1", "h2", "h3", "h4"):
                anchor = child.find("a", attrs={"name": True})
                emit("heading", _text(child, sink), level=int(name[1]), anchor=anchor["name"] if anchor else None)
            elif name == "table":
                handle_table(child)
            elif name in ("p", "li", "center") and not child.find(["table", "p", "h1", "h2", "h3", "blockquote"]):
                text = _text(child, sink)
                kind = "equation" if EQUATION_NUMBER.search(text) and len(text) < 300 else "paragraph"
                emit(kind, text)
            elif name in ("blockquote", "div", "center", "font", "ul", "ol", "p", "form", "span", "dl"):
                if not child.find(["p", "table", "h1", "h2", "h3", "li", "blockquote"]):
                    text = _text(child, sink)
                    kind = "equation" if EQUATION_NUMBER.search(text) and len(text) < 300 else "paragraph"
                    emit(kind, text)
                else:
                    handle(child)
            else:
                text = _text(child, sink)
                if text:
                    emit_text(text)
        flush_inline()

    handle(body)
    _attach_table_notes(blocks)
    if not any(b.kind == "heading" for b in blocks):
        raise ParseError("no headings found: page structure not recognised")
    return title, blocks


def _dedupe(row):
    out, last = [], None
    for oid, text in row:
        if oid != last:
            out.append((oid, text))
        last = oid
    return out


def _attach_table_notes(blocks: list[Block]) -> None:
    """Footnotes ('^1 …', '* …') and 'Primary source …' lines right after a table belong to it."""
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block.kind == "table" and block.table:
            j = i + 1
            while j < len(blocks) and blocks[j].kind in ("paragraph", "equation") and (
                    re.match(r"^(\^\s?\d+|\*|Primary source|Source|Note)", blocks[j].text)
                    # an equation or 'where …' line inside a footnote stays with that footnote
                    or (block.table.notes and (blocks[j].kind == "equation" or blocks[j].text.startswith("where")
                                               or (len(blocks[j].text) < 120 and "=" in blocks[j].text))
                        and j + 1 < len(blocks) and re.match(r"^(\^\s?\d+|where)", blocks[j + 1].text))
                    or (block.table.notes and blocks[j - 1].kind == "equation" and blocks[j].text.startswith("where"))):
                block.table.notes.append(blocks[j].text)
                block.images += blocks[j].images
                block.transcribed += blocks[j].transcribed
                del blocks[j]
        i += 1
